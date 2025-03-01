# -*- coding: utf-8 -*-
#
# docstring_comment_test.py
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

import os
import pytest
import unittest

from antlr4 import *
from antlr4.error.ErrorStrategy import BailErrorStrategy, DefaultErrorStrategy

from pynestml.generated.PyNestMLLexer import PyNestMLLexer
from pynestml.generated.PyNestMLParser import PyNestMLParser
from pynestml.meta_model.ast_nestml_compilation_unit import ASTNestMLCompilationUnit
from pynestml.meta_model.ast_neuron import ASTNeuron
from pynestml.symbol_table.symbol_table import SymbolTable
from pynestml.symbols.predefined_functions import PredefinedFunctions
from pynestml.symbols.predefined_types import PredefinedTypes
from pynestml.symbols.predefined_units import PredefinedUnits
from pynestml.symbols.predefined_variables import PredefinedVariables
from pynestml.utils.ast_source_location import ASTSourceLocation
from pynestml.utils.logger import LoggingLevel, Logger
from pynestml.visitors.ast_builder_visitor import ASTBuilderVisitor
from pynestml.visitors.ast_visitor import ASTVisitor
from pynestml.visitors.comment_collector_visitor import CommentCollectorVisitor


# setups the infrastructure
PredefinedUnits.register_units()
PredefinedTypes.register_types()
PredefinedFunctions.register_functions()
PredefinedVariables.register_variables()
SymbolTable.initialize_symbol_table(ASTSourceLocation(start_line=0, start_column=0, end_line=0, end_column=0))
Logger.init_logger(LoggingLevel.ERROR)


class DocstringCommentException(Exception):
    pass


class DocstringCommentTest(unittest.TestCase):

    def test_docstring_success(self):
        self.run_docstring_test('valid')

    @pytest.mark.xfail(strict=True, raises=DocstringCommentException)
    def test_docstring_failure(self):
        self.run_docstring_test('invalid')

    def run_docstring_test(self, case: str):
        assert case in ['valid', 'invalid']
        input_file = FileStream(
            os.path.join(os.path.realpath(os.path.join(os.path.dirname(__file__), case)),
                         'DocstringCommentTest.nestml'))
        lexer = PyNestMLLexer(input_file)
        lexer._errHandler = BailErrorStrategy()
        lexer._errHandler.reset(lexer)
        # create a token stream
        stream = CommonTokenStream(lexer)
        stream.fill()
        # parse the file
        parser = PyNestMLParser(stream)
        parser._errHandler = BailErrorStrategy()
        parser._errHandler.reset(parser)
        compilation_unit = parser.nestMLCompilationUnit()
        # now build the meta_model
        ast_builder_visitor = ASTBuilderVisitor(stream.tokens)
        ast = ast_builder_visitor.visit(compilation_unit)
        neuron_or_synapse_body_elements = ast.get_neuron_list()[0].get_body().get_body_elements()

        # now run the docstring checker visitor
        visitor = CommentCollectorVisitor(stream.tokens, strip_delim=False)
        compilation_unit.accept(visitor)
        # test whether ``"""`` is used correctly
        assert len(ast.get_neuron_list()) == 1, "Neuron failed to load correctly"

        class CommentCheckerVisitor(ASTVisitor):
            def visit(self, ast):
                for comment in ast.get_comments():
                    if "\"\"\"" in comment \
                       and not (isinstance(ast, ASTNeuron) or isinstance(ast, ASTNestMLCompilationUnit)):
                        raise DocstringCommentException()
                for comment in ast.get_post_comments():
                    if "\"\"\"" in comment:
                        raise DocstringCommentException()
        visitor = CommentCheckerVisitor()
        ast.accept(visitor)


if __name__ == '__main__':
    unittest.main()
