# -*- coding: utf-8 -*-
#
# co_co_vector_parameter_declared_in_right_block.py
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
from pynestml.cocos.co_co import CoCo
from pynestml.meta_model.ast_declaration import ASTDeclaration
from pynestml.meta_model.ast_neuron import ASTNeuron
from pynestml.meta_model.ast_variable import ASTVariable
from pynestml.symbols.symbol import SymbolKind
from pynestml.symbols.variable_symbol import BlockType
from pynestml.utils.logger import Logger, LoggingLevel
from pynestml.utils.messages import Messages
from pynestml.visitors.ast_visitor import ASTVisitor


class CoCoVectorParameterDeclaredInRightBlock(CoCo):
    """
    This CoCo ensures that the vector parameter is declared in either the parameters or internals block.
    """

    @classmethod
    def check_co_co(cls, node: ASTNeuron):
        visitor = VectorDeclarationVisitor()
        node.accept(visitor)


class VectorDeclarationVisitor(ASTVisitor):
    """
    This visitor ensures that the vector parameter is declared in the right block and has an integer type.
    """

    def visit_declaration(self, node: ASTDeclaration):

        variables = node.get_variables()
        for var in variables:
            vector_parameter = var.get_vector_parameter()
            if vector_parameter is not None:
                vector_parameter_var = ASTVariable(vector_parameter, scope=node.get_scope())
                symbol = vector_parameter_var.get_scope().resolve_to_symbol(vector_parameter_var.get_complete_name(),
                                                                            SymbolKind.VARIABLE)
                # vector parameter is a variable
                if symbol is not None:
                    if not symbol.block_type == BlockType.PARAMETERS and not symbol.block_type == BlockType.INTERNALS:
                        code, message = Messages.get_vector_parameter_wrong_block(vector_parameter_var.get_complete_name(),
                                                                                  str(symbol.block_type))
                        Logger.log_message(error_position=node.get_source_position(), log_level=LoggingLevel.ERROR,
                                           code=code, message=message)
