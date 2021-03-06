#    Copyright (c) 2015 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import inspect
import os.path

import six
from yaql.language import expressions as yaql_expressions
from yaql.language import utils
from yaql.language import yaqltypes
from yaql import yaql_interface

from murano.dsl import constants
from murano.dsl import dsl_types
from murano.dsl import helpers


NO_VALUE = utils.create_marker('NO_VALUE')


def name(dsl_name):
    def wrapper(cls):
        cls.__murano_name = dsl_name
        return cls
    return wrapper


class MuranoType(yaqltypes.PythonType):
    def __init__(self, murano_class, nullable=False, version_spec=None):
        self.murano_class = murano_class
        self.version_spec = version_spec
        super(MuranoType, self).__init__(
            (dsl_types.MuranoObject, MuranoObjectInterface), nullable)

    def check(self, value, context, *args, **kwargs):
        if not super(MuranoType, self).check(
                value, context, *args, **kwargs):
            return False
        if isinstance(value, MuranoObjectInterface):
            value = value.object
        if value is None or isinstance(value, yaql_expressions.Expression):
            return True
        murano_class = self.murano_class
        if isinstance(murano_class, six.string_types):
            murano_class_name = murano_class
        else:
            murano_class_name = murano_class.name
        return helpers.is_instance_of(
            value, murano_class_name,
            self.version_spec or helpers.get_type(context))

    def convert(self, value, sender, context, function_spec, engine,
                *args, **kwargs):
        result = super(MuranoType, self).convert(
            value, sender, context, function_spec, engine, *args, **kwargs)
        if isinstance(result, dsl_types.MuranoObject):
            return MuranoObjectInterface(result)
        return result


class ThisParameterType(yaqltypes.HiddenParameterType, yaqltypes.SmartType):
    def __init__(self):
        super(ThisParameterType, self).__init__(False)

    def convert(self, value, sender, context, function_spec, engine,
                *args, **kwargs):
        this = helpers.get_this(context)
        executor = helpers.get_executor(context)
        return MuranoObjectInterface(this, executor)


class InterfacesParameterType(yaqltypes.HiddenParameterType,
                              yaqltypes.SmartType):
    def __init__(self):
        super(InterfacesParameterType, self).__init__(False)

    def convert(self, value, sender, context, function_spec, engine,
                *args, **kwargs):
        this = helpers.get_this(context)
        return Interfaces(this)


class MuranoTypeName(yaqltypes.PythonType):
    def __init__(self, nullable=False, context=None):
        self._context = context
        super(MuranoTypeName, self).__init__(
            (dsl_types.MuranoTypeReference,
             six.string_types), nullable)

    def convert(self, value, sender, context, function_spec, engine,
                *args, **kwargs):
        context = self._context or context
        if isinstance(value, yaql_expressions.Expression):
            value = value(utils.NO_VALUE, context, engine)
        value = super(MuranoTypeName, self).convert(
            value, sender, context, function_spec, engine)
        if isinstance(value, six.string_types):
            if function_spec.meta.get(constants.META_MURANO_METHOD):
                context = helpers.get_caller_context(context)
            murano_type = helpers.get_type(context)
            value = helpers.get_class(
                murano_type.namespace_resolver.resolve_name(value),
                context).get_reference()
        return value


class MuranoObjectInterface(dsl_types.MuranoObjectInterface):
    class DataInterface(object):
        def __init__(self, object_interface):
            object.__setattr__(self, '__object_interface', object_interface)

        def __getattr__(self, item):
            oi = getattr(self, '__object_interface')
            return oi[item]

        def __setattr__(self, key, value):
            oi = getattr(self, '__object_interface')
            oi[key] = value

    class CallInterface(object):
        def __init__(self, mpl_object, executor):
            self.__object = mpl_object
            self.__executor = executor

        def __getattr__(self, item):
            executor = self.__executor or helpers.get_executor()

            def func(*args, **kwargs):
                self._insert_instruction()
                return self.__object.type.invoke(
                    item, executor, self.__object, args, kwargs,
                    helpers.get_context())
            return func

        @staticmethod
        def _insert_instruction():
            context = helpers.get_context()
            if context:
                frame = inspect.stack()[2]
                location = dsl_types.ExpressionFilePosition(
                    os.path.abspath(frame[1]), frame[2],
                    -1, frame[2], -1)
                context[constants.CTX_CURRENT_INSTRUCTION] = NativeInstruction(
                    frame[4][0].strip(), location)

    def __init__(self, mpl_object, executor=None):
        self.__object = mpl_object
        self.__executor = executor

    @property
    def object(self):
        return self.__object

    @property
    def id(self):
        return self.__object.object_id

    @property
    def owner(self):
        return self.__object.owner

    def find_owner(self, type, optional=False):
        context = helpers.get_context().create_child_context()
        yaql_engine = helpers.get_yaql_engine(context)
        context['$1'] = self.object
        context['$2'] = type
        expr_str = '$1.find($2)'
        if not optional:
            expr_str += '.require()'
        result = yaql_engine(expr_str).evaluate(context=context)
        return None if result is None else MuranoObjectInterface(result)

    @property
    def type(self):
        return self.__object.type

    @property
    def package(self):
        return self.type.package

    def data(self):
        return MuranoObjectInterface.DataInterface(self)

    @property
    def extension(self):
        return self.__object.extension

    def cast(self, murano_class, version_spec=None):
        return MuranoObjectInterface(
            helpers.cast(
                self.__object, murano_class,
                version_spec or helpers.get_type()),
            self.__executor)

    def is_instance_of(self, murano_class, version_spec=None):
        return helpers.is_instance_of(
            self.__object, murano_class,
            version_spec or helpers.get_type())

    def ancestors(self):
        return self.type.ancestors()

    def __getitem__(self, item):
        context = helpers.get_context()
        return to_mutable(
            self.__object.get_property(item, context),
            helpers.get_yaql_engine(context))

    def __setitem__(self, key, value):
        context = helpers.get_context()
        value = helpers.evaluate(value, context)
        self.__object.set_property(key, value, context)

    def __call__(self):
        return MuranoObjectInterface.CallInterface(
            self.object, self.__executor)

    def __repr__(self):
        return '<{0}>'.format(repr(self.object))


class Interfaces(object):
    def __init__(self, mpl_object):
        self.__object = mpl_object

    def yaql(self, receiver=utils.NO_VALUE):
        return yaql_interface.YaqlInterface(
            helpers.get_context(), helpers.get_yaql_engine(), receiver)

    def this(self):
        return self.methods(self.__object)

    def methods(self, mpl_object):
        if mpl_object is None:
            return None
        return MuranoObjectInterface(mpl_object)

    @property
    def execution_session(self):
        return helpers.get_execution_session()

    @property
    def caller(self):
        caller_context = helpers.get_caller_context()
        if caller_context is None:
            return None
        caller = helpers.get_this(caller_context)
        if caller is None:
            return None
        return MuranoObjectInterface(caller)

    @property
    def attributes(self):
        executor = helpers.get_executor()
        return executor.attribute_store

    @property
    def class_config(self):
        return self.__object.type.package.get_class_config(
            self.__object.type.name)

    @property
    def package_loader(self):
        return helpers.get_package_loader()


class NativeInstruction(object):
    def __init__(self, instruction, location):
        self.instruction = instruction
        self.source_file_position = location

    def __str__(self):
        return self.instruction


def to_mutable(obj, yaql_engine=None):
    if yaql_engine is None:
        yaql_engine = helpers.get_yaql_engine()

    def converter(value, limit_func, engine, rec):
        if isinstance(value, dsl_types.MuranoObject):
            return MuranoObjectInterface(value)
        else:
            return utils.convert_output_data(value, limit_func, engine, rec)

    limiter = lambda it: utils.limit_iterable(it, constants.ITERATORS_LIMIT)
    return converter(obj, limiter, yaql_engine, converter)


class MuranoObjectParameterType(yaqltypes.PythonType):
    def __init__(self, nullable=False, interface=False):
        self.interface = interface
        super(MuranoObjectParameterType, self).__init__(
            (dsl_types.MuranoObject, MuranoObjectInterface), nullable=nullable)

    def convert(self, value, *args, **kwargs):
        result = super(MuranoObjectParameterType, self).convert(
            value, *args, **kwargs)
        if result is None:
            return None
        if self.interface:
            if isinstance(result, MuranoObjectInterface):
                return result
            return MuranoObjectInterface(result)
        else:
            if isinstance(result, dsl_types.MuranoObject):
                return result
            return result.object
