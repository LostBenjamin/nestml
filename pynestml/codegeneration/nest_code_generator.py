# -*- coding: utf-8 -*-
#
# nest_code_generator.py
#
# This file is part of NEST.
#
# Copyright (C) 2004 The NEST Initiative
#
# NEST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# NEST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with NEST.  If not, see <http://www.gnu.org/licenses/>.

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import datetime

from jinja2 import TemplateRuntimeError

import odetoolbox

import pynestml
from pynestml.codegeneration.code_generator import CodeGenerator
from pynestml.codegeneration.nest_assignments_helper import NestAssignmentsHelper
from pynestml.codegeneration.nest_declarations_helper import NestDeclarationsHelper
from pynestml.codegeneration.printers.cpp_types_printer import CppTypesPrinter
from pynestml.codegeneration.printers.cpp_expression_printer import CppExpressionPrinter
from pynestml.codegeneration.printers.gsl_reference_converter import GSLReferenceConverter
from pynestml.codegeneration.printers.unitless_expression_printer import UnitlessExpressionPrinter
from pynestml.codegeneration.printers.nest_printer import NestPrinter
from pynestml.codegeneration.printers.nest_reference_converter import NESTReferenceConverter
from pynestml.codegeneration.printers.ode_toolbox_reference_converter import ODEToolboxReferenceConverter
from pynestml.frontend.frontend_configuration import FrontendConfiguration
from pynestml.meta_model.ast_assignment import ASTAssignment
from pynestml.meta_model.ast_equations_block import ASTEquationsBlock
from pynestml.meta_model.ast_input_port import ASTInputPort
from pynestml.meta_model.ast_neuron import ASTNeuron
from pynestml.meta_model.ast_kernel import ASTKernel
from pynestml.meta_model.ast_ode_equation import ASTOdeEquation
from pynestml.meta_model.ast_synapse import ASTSynapse
from pynestml.symbol_table.symbol_table import SymbolTable
from pynestml.symbols.real_type_symbol import RealTypeSymbol
from pynestml.symbols.unit_type_symbol import UnitTypeSymbol
from pynestml.symbols.symbol import SymbolKind
from pynestml.utils.ast_utils import ASTUtils
from pynestml.utils.logger import Logger
from pynestml.utils.logger import LoggingLevel
from pynestml.utils.messages import Messages
from pynestml.utils.model_parser import ModelParser
from pynestml.visitors.ast_equations_with_delay_vars_visitor import ASTEquationsWithDelayVarsVisitor
from pynestml.visitors.ast_mark_delay_vars_visitor import ASTMarkDelayVarsVisitor
from pynestml.visitors.ast_symbol_table_visitor import ASTSymbolTableVisitor
from pynestml.visitors.ast_random_number_generator_visitor import ASTRandomNumberGeneratorVisitor


def find_spiking_post_port(synapse, namespace):
    if "paired_neuron" in dir(synapse):
        for post_port_name in namespace["post_ports"]:
            if synapse.get_input_blocks() \
                    and synapse.get_input_blocks().get_input_ports() \
                    and ASTUtils.get_input_port_by_name(synapse.get_input_blocks(), post_port_name).is_spike():
                return post_port_name
    return None


class NESTCodeGenerator(CodeGenerator):
    r"""
    Code generator for a NEST Simulator (versions 3.x.x or higher) C++ extension module.

    Options:
    - **neuron_parent_class**: The C++ class from which the generated NESTML neuron class inherits. Examples: ``"ArchivingNode"``, ``"StructuralPlasticityNode"``. Default: ``"ArchivingNode"``.
    - **neuron_parent_class_include**: The C++ header filename to include that contains **neuron_parent_class**. Default: ``"archiving_node.h"``.
    - **neuron_synapse_pairs**: List of pairs of (neuron, synapse) model names.
    - **preserve_expressions**: Set to True, or a list of strings corresponding to individual variable names, to disable internal rewriting of expressions, and return same output as input expression where possible. Only applies to variables specified as first-order differential equations. (This parameter is passed to ODE-toolbox.)
    - **simplify_expression**: For all expressions ``expr`` that are rewritten by ODE-toolbox: the contents of this parameter string are ``eval()``ed in Python to obtain the final output expression. Override for custom expression simplification steps. Example: ``sympy.simplify(expr)``. Default: ``"sympy.logcombine(sympy.powsimp(sympy.expand(expr)))"``. (This parameter is passed to ODE-toolbox.)
    - **templates**: Path containing jinja templates used to generate code for NEST simulator.
        - **path**: Path containing jinja templates used to generate code for NEST simulator.
        - **model_templates**: A list of the jinja templates or a relative path to a directory containing the neuron and synapse model templates.
            - **neuron**: A list of neuron model jinja templates.
            - **synapse**: A list of synapse model jinja templates.
        - **module_templates**: A list of the jinja templates or a relative path to a directory containing the templates related to generating the NEST module.
    """

    _default_options = {
        "neuron_parent_class": "ArchivingNode",
        "neuron_parent_class_include": "archiving_node.h",
        "neuron_synapse_pairs": [],
        "preserve_expressions": False,
        "simplify_expression": "sympy.logcombine(sympy.powsimp(sympy.expand(expr)))",
        "templates": {
            "path": "point_neuron",
            "model_templates": {
                "neuron": ["@NEURON_NAME@.cpp.jinja2", "@NEURON_NAME@.h.jinja2"],
                "synapse": ["@SYNAPSE_NAME@.h.jinja2"]
            },
            "module_templates": ["setup"]
        }
    }

    def __init__(self, options: Optional[Mapping[str, Any]] = None):
        super().__init__("NEST", options)
        self.analytic_solver = {}
        self.numeric_solver = {}
        self.non_equations_state_variables = {}  # those state variables not defined as an ODE in the equations block

        self.setup_template_env()

        self._types_printer = CppTypesPrinter()
        self._gsl_reference_converter = GSLReferenceConverter()
        self._nest_reference_converter = NESTReferenceConverter()
        self._nest_reference_converter_no_origin = NESTReferenceConverter()
        self._nest_reference_converter_no_origin.with_origin = False

        self._printer = CppExpressionPrinter(self._nest_reference_converter)
        self._unitless_expression_printer = UnitlessExpressionPrinter(self._nest_reference_converter)
        self._unitless_expression_printer_no_origin = UnitlessExpressionPrinter(self._nest_reference_converter_no_origin)
        self._gsl_printer = UnitlessExpressionPrinter(reference_converter=self._gsl_reference_converter)

        self._nest_printer = NestPrinter(reference_converter=self._nest_reference_converter,
                                         types_printer=self._types_printer,
                                         expression_printer=self._printer)

        self._unitless_nest_gsl_printer = NestPrinter(reference_converter=self._nest_reference_converter,
                                                      types_printer=self._types_printer,
                                                      expression_printer=self._unitless_expression_printer)

        self._ode_toolbox_printer = UnitlessExpressionPrinter(ODEToolboxReferenceConverter())

    def raise_helper(self, msg):
        raise TemplateRuntimeError(msg)

    def set_options(self, options: Mapping[str, Any]) -> Mapping[str, Any]:
        ret = super().set_options(options)
        self.setup_template_env()

        return ret

    def generate_code(self, models: Sequence[Union[ASTNeuron, ASTSynapse]]) -> None:
        neurons = [model for model in models if isinstance(model, ASTNeuron)]
        synapses = [model for model in models if isinstance(model, ASTSynapse)]
        self.analyse_transform_neurons(neurons)
        self.analyse_transform_synapses(synapses)
        self.generate_neurons(neurons)
        self.generate_synapses(synapses)
        self.generate_module_code(neurons, synapses)

        for astnode in neurons + synapses:
            if Logger.has_errors(astnode):
                raise Exception("Error(s) occurred during code generation")

    def _get_module_namespace(self, neurons: List[ASTNeuron], synapses: List[ASTSynapse]) -> Dict:
        """
        Creates a namespace for generating NEST extension module code
        :param neurons: List of neurons
        :return: a context dictionary for rendering templates
        """
        namespace = {"neurons": neurons,
                     "synapses": synapses,
                     "moduleName": FrontendConfiguration.get_module_name(),
                     "now": datetime.datetime.utcnow()}
        return namespace

    def analyse_transform_neurons(self, neurons: List[ASTNeuron]) -> None:
        """
        Analyse and transform a list of neurons.
        :param neurons: a list of neurons.
        """
        for neuron in neurons:
            code, message = Messages.get_analysing_transforming_neuron(neuron.get_name())
            Logger.log_message(None, code, message, None, LoggingLevel.INFO)
            spike_updates, post_spike_updates, equations_with_delay_vars = self.analyse_neuron(neuron)
            neuron.spike_updates = spike_updates
            neuron.post_spike_updates = post_spike_updates
            neuron.equations_with_delay_vars = equations_with_delay_vars

    def analyse_transform_synapses(self, synapses: List[ASTSynapse]) -> None:
        """
        Analyse and transform a list of synapses.
        :param synapses: a list of synapses.
        """
        for synapse in synapses:
            if Logger.logging_level == LoggingLevel.INFO:
                print("Analysing/transforming synapse {}.".format(synapse.get_name()))
            spike_updates = self.analyse_synapse(synapse)
            synapse.spike_updates = spike_updates

    def analyse_neuron(self, neuron: ASTNeuron) -> Tuple[Dict[str, ASTAssignment], Dict[str, ASTAssignment],
                                                         List[ASTOdeEquation]]:
        """
        Analyse and transform a single neuron.
        :param neuron: a single neuron.
        :return: see documentation for get_spike_update_expressions() for more information.
        :return: post_spike_updates: list of post-synaptic spike update expressions
        :return: equations_with_delay_vars: list of equations containing delay variables
        """
        code, message = Messages.get_start_processing_model(neuron.get_name())
        Logger.log_message(neuron, code, message, neuron.get_source_position(), LoggingLevel.INFO)

        equations_block = neuron.get_equations_block()

        if equations_block is None:
            # add all declared state variables as none of them are used in equations block
            self.non_equations_state_variables[neuron.get_name()] = []
            self.non_equations_state_variables[neuron.get_name()].extend(
                ASTUtils.all_variables_defined_in_block(neuron.get_state_blocks()))

            return [], [], []

        delta_factors = ASTUtils.get_delta_factors_(neuron, equations_block)
        kernel_buffers = ASTUtils.generate_kernel_buffers_(neuron, equations_block)
        ASTUtils.replace_convolve_calls_with_buffers_(neuron, equations_block)
        ASTUtils.make_inline_expressions_self_contained(equations_block.get_inline_expressions())
        ASTUtils.replace_inline_expressions_through_defining_expressions(
            equations_block.get_ode_equations(), equations_block.get_inline_expressions())

        # Collect all equations with delay variables and replace ASTFunctionCall to ASTVariable wherever necessary
        equations_with_delay_vars_visitor = ASTEquationsWithDelayVarsVisitor()
        neuron.accept(equations_with_delay_vars_visitor)
        equations_with_delay_vars = equations_with_delay_vars_visitor.equations

        analytic_solver, numeric_solver = self.ode_toolbox_analysis(neuron, kernel_buffers)
        self.analytic_solver[neuron.get_name()] = analytic_solver
        self.numeric_solver[neuron.get_name()] = numeric_solver

        self.non_equations_state_variables[neuron.get_name()] = []
        for decl in neuron.get_state_blocks().get_declarations():
            for var in decl.get_variables():
                # check if this variable is not in equations
                if not neuron.get_equations_blocks():
                    self.non_equations_state_variables[neuron.get_name()].append(var)
                    continue

                used_in_eq = False
                for ode_eq in neuron.get_equations_blocks().get_ode_equations():
                    if ode_eq.get_lhs().get_name() == var.get_name():
                        used_in_eq = True
                        break
                for kern in neuron.get_equations_blocks().get_kernels():
                    for kern_var in kern.get_variables():
                        if kern_var.get_name() == var.get_name():
                            used_in_eq = True
                            break

                if not used_in_eq:
                    self.non_equations_state_variables[neuron.get_name()].append(var)

        ASTUtils.remove_initial_values_for_kernels(neuron)
        kernels = ASTUtils.remove_kernel_definitions_from_equations_block(neuron)
        ASTUtils.update_initial_values_for_odes(neuron, [analytic_solver, numeric_solver])
        ASTUtils.remove_ode_definitions_from_equations_block(neuron)
        ASTUtils.create_initial_values_for_kernels(neuron, [analytic_solver, numeric_solver], kernels)
        ASTUtils.replace_variable_names_in_expressions(neuron, [analytic_solver, numeric_solver])
        ASTUtils.replace_convolution_aliasing_inlines(neuron)
        ASTUtils.add_timestep_symbol(neuron)

        if self.analytic_solver[neuron.get_name()] is not None:
            neuron = ASTUtils.add_declarations_to_internals(
                neuron, self.analytic_solver[neuron.get_name()]["propagators"])

        state_vars_before_update = neuron.get_state_symbols()
        self.update_symbol_table(neuron)

        # Update the delay parameter parameters after symbol table update
        ASTUtils.update_delay_parameter_in_state_vars(neuron, state_vars_before_update)

        spike_updates, post_spike_updates = self.get_spike_update_expressions(
            neuron, kernel_buffers, [analytic_solver, numeric_solver], delta_factors)

        return spike_updates, post_spike_updates, equations_with_delay_vars

    def analyse_synapse(self, synapse: ASTSynapse) -> Dict[str, ASTAssignment]:
        """
        Analyse and transform a single synapse.
        :param synapse: a single synapse.
        """
        code, message = Messages.get_start_processing_model(synapse.get_name())
        Logger.log_message(synapse, code, message, synapse.get_source_position(), LoggingLevel.INFO)

        equations_block = synapse.get_equations_block()
        spike_updates = {}
        if equations_block is not None:
            delta_factors = ASTUtils.get_delta_factors_(synapse, equations_block)
            kernel_buffers = ASTUtils.generate_kernel_buffers_(synapse, equations_block)
            ASTUtils.replace_convolve_calls_with_buffers_(synapse, equations_block)
            ASTUtils.make_inline_expressions_self_contained(equations_block.get_inline_expressions())
            ASTUtils.replace_inline_expressions_through_defining_expressions(
                equations_block.get_ode_equations(), equations_block.get_inline_expressions())

            analytic_solver, numeric_solver = self.ode_toolbox_analysis(synapse, kernel_buffers)
            self.analytic_solver[synapse.get_name()] = analytic_solver
            self.numeric_solver[synapse.get_name()] = numeric_solver

            ASTUtils.remove_initial_values_for_kernels(synapse)
            kernels = ASTUtils.remove_kernel_definitions_from_equations_block(synapse)
            ASTUtils.update_initial_values_for_odes(synapse, [analytic_solver, numeric_solver])
            ASTUtils.remove_ode_definitions_from_equations_block(synapse)
            ASTUtils.create_initial_values_for_kernels(synapse, [analytic_solver, numeric_solver], kernels)
            ASTUtils.replace_variable_names_in_expressions(synapse, [analytic_solver, numeric_solver])
            ASTUtils.add_timestep_symbol(synapse)
            self.update_symbol_table(synapse)

            if not self.analytic_solver[synapse.get_name()] is None:
                synapse = ASTUtils.add_declarations_to_internals(
                    synapse, self.analytic_solver[synapse.get_name()]["propagators"])

            self.update_symbol_table(synapse)
            spike_updates, _ = self.get_spike_update_expressions(
                synapse, kernel_buffers, [analytic_solver, numeric_solver], delta_factors)
        else:
            ASTUtils.add_timestep_symbol(synapse)

        ASTUtils.update_blocktype_for_common_parameters(synapse)

        return spike_updates

    def _get_synapse_model_namespace(self, synapse: ASTSynapse) -> Dict:
        """
        Returns a standard namespace with often required functionality.
        :param synapse: a single synapse instance
        :return: a map from name to functionality.
        :rtype: dict
        """
        namespace = dict()

        if "paired_neuron" in dir(synapse):
            # synapse is being co-generated with neuron
            namespace["paired_neuron"] = synapse.paired_neuron.get_name()
            namespace["post_ports"] = synapse.post_port_names

            namespace["spiking_post_ports"] = synapse.spiking_post_port_names
            namespace["vt_ports"] = synapse.vt_port_names
            all_input_port_names = [p.name for p in synapse.get_input_blocks().get_input_ports()]
            namespace["pre_ports"] = list(set(all_input_port_names)
                                          - set(namespace["post_ports"]) - set(namespace["vt_ports"]))
        else:
            # separate (not neuron+synapse co-generated)
            all_input_port_names = [p.name for p in synapse.get_input_blocks().get_input_ports()]
            namespace["pre_ports"] = all_input_port_names

        assert len(namespace["pre_ports"]) <= 1, "Synapses only support one spiking input port"

        namespace["synapseName"] = synapse.get_name()
        namespace["synapse"] = synapse
        namespace["astnode"] = synapse
        namespace["moduleName"] = FrontendConfiguration.get_module_name()
        namespace["printer"] = self._unitless_nest_gsl_printer
        namespace["assignments"] = NestAssignmentsHelper()
        namespace["names"] = self._nest_reference_converter
        namespace["declarations"] = NestDeclarationsHelper(self._types_printer)
        namespace["utils"] = ASTUtils
        namespace["idemPrinter"] = self._printer
        namespace["printerGSL"] = self._gsl_printer
        namespace["now"] = datetime.datetime.utcnow()
        namespace["tracing"] = FrontendConfiguration.is_dev
        namespace["has_state_vectors"] = False
        namespace["vector_symbols"] = []
        namespace['has_delay_variables'] = synapse.has_delay_variables()
        namespace['names_namespace'] = synapse.get_name() + "_names"

        # event handlers priority
        # XXX: this should be refactored in case we have additional modulatory (3rd-factor) spiking input ports in the synapse
        namespace["pre_before_post_update"] = 0   # C++-compatible boolean...
        spiking_post_port = find_spiking_post_port(synapse, namespace)
        if spiking_post_port:
            post_spike_port_priority = None
            if "priority" in synapse.get_on_receive_block(spiking_post_port).get_const_parameters().keys():
                post_spike_port_priority = int(synapse.get_on_receive_block(
                    spiking_post_port).get_const_parameters()["priority"])

            if post_spike_port_priority \
                    and len(namespace["pre_ports"]) and len(namespace["post_ports"]) \
                    and "priority" in synapse.get_on_receive_block(namespace["pre_ports"][0]).get_const_parameters().keys() \
                    and int(synapse.get_on_receive_block(namespace["pre_ports"][0]).get_const_parameters()["priority"]) < post_spike_port_priority:
                namespace["pre_before_post_update"] = 1   # C++-compatible boolean...

        namespace["PredefinedUnits"] = pynestml.symbols.predefined_units.PredefinedUnits
        namespace["UnitTypeSymbol"] = pynestml.symbols.unit_type_symbol.UnitTypeSymbol
        namespace["SymbolKind"] = pynestml.symbols.symbol.SymbolKind

        namespace["initial_values"] = {}
        namespace["variable_symbols"] = {}
        namespace["uses_analytic_solver"] = synapse.get_name() in self.analytic_solver.keys() \
            and self.analytic_solver[synapse.get_name()] is not None
        if namespace["uses_analytic_solver"]:
            namespace["analytic_state_variables"] = self.analytic_solver[synapse.get_name()]["state_variables"]
            namespace["variable_symbols"].update({sym: synapse.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace["analytic_state_variables"]})
            namespace["update_expressions"] = {}
            for sym, expr in self.analytic_solver[synapse.get_name()]["initial_values"].items():
                namespace["initial_values"][sym] = expr
            for sym in namespace["analytic_state_variables"]:
                expr_str = self.analytic_solver[synapse.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present,
                # as differential equations must have been defined to get here
                expr_ast.update_scope(synapse.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace["update_expressions"][sym] = expr_ast

            namespace["propagators"] = self.analytic_solver[synapse.get_name()]["propagators"]

        namespace["uses_numeric_solver"] = synapse.get_name() in self.numeric_solver.keys() \
            and self.numeric_solver[synapse.get_name()] is not None
        if namespace["uses_numeric_solver"]:
            namespace["numeric_state_variables"] = self.numeric_solver[synapse.get_name()]["state_variables"]
            namespace["variable_symbols"].update({sym: synapse.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace["numeric_state_variables"]})
            assert not any([sym is None for sym in namespace["variable_symbols"].values()])
            namespace["numeric_update_expressions"] = {}
            for sym, expr in self.numeric_solver[synapse.get_name()]["initial_values"].items():
                namespace["initial_values"][sym] = expr
            for sym in namespace["numeric_state_variables"]:
                expr_str = self.numeric_solver[synapse.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present,
                # as differential equations must have been defined to get here
                expr_ast.update_scope(synapse.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace["numeric_update_expressions"][sym] = expr_ast

            namespace["names"] = self._gsl_reference_converter
            namespace["printer"] = self._unitless_nest_gsl_printer

        namespace["spike_updates"] = synapse.spike_updates

        rng_visitor = ASTRandomNumberGeneratorVisitor()
        synapse.accept(rng_visitor)
        namespace["norm_rng"] = rng_visitor._norm_rng_is_used

        namespace["PyNestMLLexer"] = {}
        from pynestml.generated.PyNestMLLexer import PyNestMLLexer
        for kw in dir(PyNestMLLexer):
            if kw.isupper():
                namespace["PyNestMLLexer"][kw] = eval("PyNestMLLexer." + kw)

        return namespace

    def _get_neuron_model_namespace(self, neuron: ASTNeuron) -> Dict:
        """
        Returns a standard namespace with often required functionality.
        :param neuron: a single neuron instance
        :type neuron: ASTNeuron
        :return: a map from name to functionality.
        :rtype: dict
        """
        namespace = dict()

        if "paired_synapse" in dir(neuron):
            namespace["paired_synapse"] = neuron.paired_synapse.get_name()
            namespace["post_spike_updates"] = neuron.post_spike_updates
            if "moved_spike_updates" in dir(neuron):
                namespace["spike_update_stmts"] = neuron.moved_spike_updates
            else:
                namespace["spike_update_stmts"] = []
            namespace["transferred_variables"] = neuron._transferred_variables
            namespace["transferred_variables_syms"] = {var_name: neuron.scope.resolve_to_symbol(
                var_name, SymbolKind.VARIABLE) for var_name in namespace["transferred_variables"]}
            assert not any([v is None for v in namespace["transferred_variables_syms"].values()])
            # {var_name: ASTUtils.get_declaration_by_name(neuron.get_initial_values_blocks(), var_name) for var_name in namespace["transferred_variables"]}
        namespace["neuronName"] = neuron.get_name()
        namespace["neuron"] = neuron
        namespace["astnode"] = neuron
        namespace["moduleName"] = FrontendConfiguration.get_module_name()
        namespace["printer"] = self._unitless_nest_gsl_printer
        namespace["nest_printer"] = self._nest_printer
        namespace["assignments"] = NestAssignmentsHelper()
        namespace["names"] = self._nest_reference_converter
        namespace["declarations"] = NestDeclarationsHelper(self._types_printer)
        namespace["utils"] = ASTUtils
        namespace["idemPrinter"] = self._printer
        namespace["outputEvent"] = namespace["printer"].print_output_event(neuron.get_body())
        namespace["has_spike_input"] = ASTUtils.has_spike_input(neuron.get_body())
        namespace["has_continuous_input"] = ASTUtils.has_continuous_input(neuron.get_body())
        namespace["printerGSL"] = self._gsl_printer
        namespace["now"] = datetime.datetime.utcnow()
        namespace["tracing"] = FrontendConfiguration.is_dev
        namespace["has_state_vectors"] = neuron.has_state_vectors()
        namespace["vector_symbols"] = neuron.get_vector_symbols()
        namespace["has_delay_variables"] = neuron.has_delay_variables()
        namespace["names_namespace"] = neuron.get_name() + "_names"

        namespace["neuron_parent_class"] = self.get_option("neuron_parent_class")
        namespace["neuron_parent_class_include"] = self.get_option("neuron_parent_class_include")

        namespace["PredefinedUnits"] = pynestml.symbols.predefined_units.PredefinedUnits
        namespace["UnitTypeSymbol"] = pynestml.symbols.unit_type_symbol.UnitTypeSymbol
        namespace["SymbolKind"] = pynestml.symbols.symbol.SymbolKind

        namespace["initial_values"] = {}
        namespace["variable_symbols"] = {}

        namespace["uses_analytic_solver"] = neuron.get_name() in self.analytic_solver.keys() \
            and self.analytic_solver[neuron.get_name()] is not None
        if namespace["uses_analytic_solver"]:
            namespace["analytic_state_variables_moved"] = []
            if "paired_synapse" in dir(neuron):
                namespace["analytic_state_variables"] = []
                for sv in self.analytic_solver[neuron.get_name()]["state_variables"]:
                    moved = False
                    for mv in neuron.recursive_vars_used:
                        name_snip = mv + "__"
                        if name_snip == sv[:len(name_snip)]:
                            # this variable was moved from synapse to neuron
                            if not sv in namespace["analytic_state_variables_moved"]:
                                namespace["analytic_state_variables_moved"].append(sv)
                                moved = True
                    if not moved:
                        namespace["analytic_state_variables"].append(sv)
                namespace["variable_symbols"].update({sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                    sym, SymbolKind.VARIABLE) for sym in namespace["analytic_state_variables_moved"]})
            else:
                namespace["analytic_state_variables"] = self.analytic_solver[neuron.get_name()]["state_variables"]
            namespace["variable_symbols"].update({sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace["analytic_state_variables"]})

            namespace["update_expressions"] = {}
            for sym, expr in self.analytic_solver[neuron.get_name()]["initial_values"].items():
                namespace["initial_values"][sym] = expr
            for sym in namespace["analytic_state_variables"] + namespace["analytic_state_variables_moved"]:
                expr_str = self.analytic_solver[neuron.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present,
                # as differential equations must have been defined to get here
                expr_ast.update_scope(neuron.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace["update_expressions"][sym] = expr_ast

                # Check if the update expression has delay variables
                if ASTUtils.has_equation_with_delay_variable(neuron.equations_with_delay_vars, sym):
                    marks_delay_vars_visitor = ASTMarkDelayVarsVisitor()
                    expr_ast.accept(marks_delay_vars_visitor)

            namespace["propagators"] = self.analytic_solver[neuron.get_name()]["propagators"]

            namespace["propagators_are_state_dependent"] = False
            for prop_name, prop_expr in namespace["propagators"].items():
                prop_expr_ast = ModelParser.parse_expression(prop_expr)

                for var_sym in neuron.get_state_symbols():
                    if var_sym.get_symbol_name() in [var.get_name() for var in prop_expr_ast.get_variables()]:
                        namespace["propagators_are_state_dependent"] = True

        # convert variables from ASTVariable instances to strings
        _names = self.non_equations_state_variables[neuron.get_name()]
        _names = [ASTUtils.to_ode_toolbox_processed_name(var.get_complete_name()) for var in _names]
        namespace["non_equations_state_variables"] = _names

        namespace["uses_numeric_solver"] = neuron.get_name() in self.numeric_solver.keys() \
            and self.numeric_solver[neuron.get_name()] is not None
        if namespace["uses_numeric_solver"]:
            namespace["numeric_state_variables_moved"] = []
            if "paired_synapse" in dir(neuron):
                namespace["numeric_state_variables"] = []
                for sv in self.numeric_solver[neuron.get_name()]["state_variables"]:
                    moved = False
                    for mv in neuron.recursive_vars_used:
                        name_snip = mv + "__"
                        if name_snip == sv[:len(name_snip)]:
                            # this variable was moved from synapse to neuron
                            if not sv in namespace["numeric_state_variables_moved"]:
                                namespace["numeric_state_variables_moved"].append(sv)
                                moved = True
                    if not moved:
                        namespace["numeric_state_variables"].append(sv)
                namespace["variable_symbols"].update({sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                    sym, SymbolKind.VARIABLE) for sym in namespace["numeric_state_variables_moved"]})
            else:
                namespace["numeric_state_variables"] = self.numeric_solver[neuron.get_name()]["state_variables"]

            namespace["variable_symbols"].update({sym: neuron.get_equations_block().get_scope().resolve_to_symbol(
                sym, SymbolKind.VARIABLE) for sym in namespace["numeric_state_variables"]})
            assert not any([sym is None for sym in namespace["variable_symbols"].values()])
            for sym, expr in self.numeric_solver[neuron.get_name()]["initial_values"].items():
                namespace["initial_values"][sym] = expr
            namespace["numeric_update_expressions"] = {}
            for sym in namespace["numeric_state_variables"] + namespace["numeric_state_variables_moved"]:
                expr_str = self.numeric_solver[neuron.get_name()]["update_expressions"][sym]
                expr_ast = ModelParser.parse_expression(expr_str)
                # pretend that update expressions are in "equations" block, which should always be present,
                # as differential equations must have been defined to get here
                expr_ast.update_scope(neuron.get_equations_blocks().get_scope())
                expr_ast.accept(ASTSymbolTableVisitor())
                namespace["numeric_update_expressions"][sym] = expr_ast

                # Check if the update expression has delay variables
                if ASTUtils.has_equation_with_delay_variable(neuron.equations_with_delay_vars, sym):
                    marks_delay_vars_visitor = ASTMarkDelayVarsVisitor()
                    expr_ast.accept(marks_delay_vars_visitor)

            if namespace["uses_numeric_solver"]:
                if "analytic_state_variables_moved" in namespace.keys():
                    namespace["purely_numeric_state_variables_moved"] = list(
                        set(namespace["numeric_state_variables_moved"]) - set(namespace["analytic_state_variables_moved"]))

                else:
                    namespace["purely_numeric_state_variables_moved"] = namespace["numeric_state_variables_moved"]

            namespace["names"] = self._gsl_reference_converter
            namespace["printer"] = self._unitless_nest_gsl_printer
        namespace["spike_updates"] = neuron.spike_updates

        namespace["recordable_state_variables"] = [sym for sym in neuron.get_state_symbols()
                                                   if isinstance(sym.get_type_symbol(), (UnitTypeSymbol, RealTypeSymbol))
                                                   and sym.is_recordable
                                                   and not ASTUtils.is_delta_kernel(neuron.get_kernel_by_name(sym.name))]

        namespace["recordable_inline_expressions"] = [sym for sym in neuron.get_inline_expression_symbols()
                                                      if isinstance(sym.get_type_symbol(), (UnitTypeSymbol, RealTypeSymbol))
                                                      and sym.is_recordable]

        namespace["parameter_syms_with_iv"] = [sym for sym in neuron.get_parameter_symbols()
                                               if sym.has_declaring_expression()
                                               and (not neuron.get_kernel_by_name(sym.name))]

        rng_visitor = ASTRandomNumberGeneratorVisitor()
        neuron.accept(rng_visitor)
        namespace["norm_rng"] = rng_visitor._norm_rng_is_used

        return namespace

    def ode_toolbox_analysis(self, neuron: ASTNeuron, kernel_buffers: Mapping[ASTKernel, ASTInputPort]):
        """
        Prepare data for ODE-toolbox input format, invoke ODE-toolbox analysis via its API, and return the output.
        """
        assert isinstance(neuron.get_equations_blocks(), ASTEquationsBlock), "only one equation block should be present"

        equations_block = neuron.get_equations_block()

        if len(equations_block.get_kernels()) == 0 and len(equations_block.get_ode_equations()) == 0:
            # no equations defined -> no changes to the neuron
            return None, None

        parameters_block = neuron.get_parameter_blocks()
        odetoolbox_indict = ASTUtils.transform_ode_and_kernels_to_json(neuron, parameters_block, kernel_buffers, printer=self._ode_toolbox_printer)
        odetoolbox_indict["options"] = {}
        odetoolbox_indict["options"]["output_timestep_symbol"] = "__h"
        solver_result = odetoolbox.analysis(odetoolbox_indict,
                                            disable_stiffness_check=True,
                                            preserve_expressions=self.get_option("preserve_expressions"),
                                            simplify_expression=self.get_option("simplify_expression"),
                                            log_level=FrontendConfiguration.logging_level)
        analytic_solver = None
        analytic_solvers = [x for x in solver_result if x["solver"] == "analytical"]
        assert len(analytic_solvers) <= 1, "More than one analytic solver not presently supported"
        if len(analytic_solvers) > 0:
            analytic_solver = analytic_solvers[0]

        # if numeric solver is required, generate a stepping function that includes each state variable, including the analytic ones
        numeric_solver = None
        numeric_solvers = [x for x in solver_result if x["solver"].startswith("numeric")]
        if numeric_solvers:
            solver_result = odetoolbox.analysis(odetoolbox_indict,
                                                disable_stiffness_check=True,
                                                disable_analytic_solver=True,
                                                preserve_expressions=self.get_option("preserve_expressions"),
                                                simplify_expression=self.get_option("simplify_expression"),
                                                log_level=FrontendConfiguration.logging_level)
            numeric_solvers = [x for x in solver_result if x["solver"].startswith("numeric")]
            assert len(numeric_solvers) <= 1, "More than one numeric solver not presently supported"
            if len(numeric_solvers) > 0:
                numeric_solver = numeric_solvers[0]

        return analytic_solver, numeric_solver

    def update_symbol_table(self, neuron) -> None:
        """
        Update symbol table and scope.
        """
        SymbolTable.delete_neuron_scope(neuron.get_name())
        symbol_table_visitor = ASTSymbolTableVisitor()
        symbol_table_visitor.after_ast_rewrite_ = True
        neuron.accept(symbol_table_visitor)
        SymbolTable.add_neuron_scope(neuron.get_name(), neuron.get_scope())

    def get_spike_update_expressions(self, neuron: ASTNeuron, kernel_buffers, solver_dicts, delta_factors) -> Tuple[Dict[str, ASTAssignment], Dict[str, ASTAssignment]]:
        r"""
        Generate the equations that update the dynamical variables when incoming spikes arrive. To be invoked after
        ode-toolbox.

        For example, a resulting `assignment_str` could be "I_kernel_in += (inh_spikes/nS) * 1". The values are taken from the initial values for each corresponding dynamical variable, either from ode-toolbox or directly from user specification in the model.
        from the initial values for each corresponding dynamical variable, either from ode-toolbox or directly from
        user specification in the model.

        Note that for kernels, `initial_values` actually contains the increment upon spike arrival, rather than the
        initial value of the corresponding ODE dimension.
        ``spike_updates`` is a mapping from input port name (as a string) to update expressions.

        ``post_spike_updates`` is a mapping from kernel name (as a string) to update expressions.
        """
        spike_updates = {}
        post_spike_updates = {}

        for kernel, spike_input_port in kernel_buffers:
            if ASTUtils.is_delta_kernel(kernel):
                continue

            if not str(spike_input_port) in spike_updates.keys():
                spike_updates[str(spike_input_port)] = []

            if "_is_post_port" in dir(spike_input_port.get_variable()) \
               and spike_input_port.get_variable()._is_post_port:
                # it's a port in the neuron ??? that receives post spikes ???
                orig_port_name = str(spike_input_port)[:str(spike_input_port).index("__for_")]
                buffer_type = neuron.paired_synapse.get_scope().resolve_to_symbol(orig_port_name, SymbolKind.VARIABLE).get_type_symbol()
            else:
                buffer_type = neuron.get_scope().resolve_to_symbol(str(spike_input_port), SymbolKind.VARIABLE).get_type_symbol()

            assert not buffer_type is None

            for kernel_var in kernel.get_variables():
                for var_order in range(ASTUtils.get_kernel_var_order_from_ode_toolbox_result(kernel_var.get_name(), solver_dicts)):
                    kernel_spike_buf_name = ASTUtils.construct_kernel_X_spike_buf_name(
                        kernel_var.get_name(), spike_input_port, var_order)
                    expr = ASTUtils.get_initial_value_from_ode_toolbox_result(
                        kernel_spike_buf_name, solver_dicts)
                    assert expr is not None, "Initial value not found for kernel " + kernel_var
                    expr = str(expr)
                    if expr in ["0", "0.", "0.0"]:
                        continue    # skip adding the statement if we are only adding zero

                    assignment_str = kernel_spike_buf_name + " += "
                    if "_is_post_port" in dir(spike_input_port.get_variable()) \
                       and spike_input_port.get_variable()._is_post_port:
                        assignment_str += "1."
                    else:
                        assignment_str += "(" + str(spike_input_port) + ")"
                    if not expr in ["1.", "1.0", "1"]:
                        assignment_str += " * (" + expr + ")"

                    if not buffer_type.print_nestml_type() in ["1.", "1.0", "1", "real", "integer"]:
                        assignment_str += " / (" + buffer_type.print_nestml_type() + ")"

                    ast_assignment = ModelParser.parse_assignment(assignment_str)
                    ast_assignment.update_scope(neuron.get_scope())
                    ast_assignment.accept(ASTSymbolTableVisitor())

                    if neuron.get_scope().resolve_to_symbol(str(spike_input_port), SymbolKind.VARIABLE) is None:
                        # this case covers variables that were moved from synapse to the neuron
                        post_spike_updates[kernel_var.get_name()] = ast_assignment
                    elif "_is_post_port" in dir(spike_input_port.get_variable()) and spike_input_port.get_variable()._is_post_port:
                        print("adding post assignment string: " + str(ast_assignment))
                        spike_updates[str(spike_input_port)].append(ast_assignment)
                    else:
                        spike_updates[str(spike_input_port)].append(ast_assignment)

        for k, factor in delta_factors.items():
            var = k[0]
            inport = k[1]
            assignment_str = var.get_name() + "'" * (var.get_differential_order() - 1) + " += "
            if not factor in ["1.", "1.0", "1"]:
                factor_expr = ModelParser.parse_expression(factor)
                factor_expr.update_scope(neuron.get_scope())
                factor_expr.accept(ASTSymbolTableVisitor())
                assignment_str += "(" + self._unitless_expression_printer_no_origin.print_expression(factor_expr) + ") * "

            assignment_str += str(inport)
            ast_assignment = ModelParser.parse_assignment(assignment_str)
            ast_assignment.update_scope(neuron.get_scope())
            ast_assignment.accept(ASTSymbolTableVisitor())

            if not str(inport) in spike_updates.keys():
                spike_updates[str(inport)] = []

            spike_updates[str(inport)].append(ast_assignment)

        return spike_updates, post_spike_updates
