#    Copyright (c) 2016 Mirantis, Inc.
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

import semantic_version
from yaql.language import specs
from yaql import yaqlization

from murano.dsl import dsl
from murano.dsl import dsl_types
from murano.dsl import helpers


@specs.yaql_property(dsl_types.MuranoClass)
@specs.name('name')
def class_name(murano_class):
    return murano_class.name


@specs.yaql_property(dsl_types.MuranoClass)
def methods(murano_class):
    all_method_names = murano_class.all_method_names
    return tuple(
        murano_method
        for name in all_method_names
        if not name.startswith('__') and not name.startswith('.')
        for murano_method in murano_class.find_method(name)
    )


@specs.yaql_property(dsl_types.MuranoClass)
def properties(murano_class):
    all_property_names = murano_class.all_property_names
    return tuple(
        prop
        for prop_name in all_property_names
        if not prop_name.startswith('__') and not prop_name.startswith('.')
        for prop in murano_class.find_property(prop_name)
    )


@specs.yaql_property(dsl_types.MuranoClass)
def ancestors(murano_class):
    return tuple(murano_class.ancestors())


@specs.yaql_property(dsl_types.MuranoClass)
def package(murano_class):
    return murano_class.package


@specs.yaql_property(dsl_types.MuranoClass)
@specs.name('version')
def class_version(murano_class):
    return murano_class.version


@specs.yaql_property(dsl_types.MuranoProperty)
@specs.name('name')
def property_name(murano_property):
    return murano_property.name


# TODO(ativelkov): add 'default' to return some wrapped YAQL expression
# @specs.yaql_property(dsl_types.MuranoProperty)
# @specs.name('default')
# def property_default(murano_property):
#     return murano_property.default


@specs.yaql_property(dsl_types.MuranoProperty)
@specs.name('has_default')
def property_has_default(murano_property):
    return murano_property.has_default


@specs.yaql_property(dsl_types.MuranoProperty)
@specs.name('usage')
def property_usage(murano_property):
    return murano_property.usage


@specs.yaql_property(dsl_types.MuranoProperty)
@specs.name('declaring_type')
def property_owner(murano_property):
    return murano_property.murano_class


@specs.name('get_value')
@specs.parameter('property_', dsl_types.MuranoProperty)
@specs.parameter('object_', dsl.MuranoObjectParameterType(nullable=True))
@specs.method
def property_get_value(context, property_, object_):
    if object_ is None:
        return property_.murano_class.get_property(
            name=property_.name, context=context)
    return object_.cast(property_.murano_class).get_property(
        name=property_.name, context=context)


@specs.name('set_value')
@specs.parameter('property_', dsl_types.MuranoProperty)
@specs.parameter('object_', dsl.MuranoObjectParameterType(nullable=True))
@specs.method
def property_set_value(context, property_, object_, value):
    if object_ is None:
        property_.murano_class.set_property(
            name=property_.name, value=value, context=context)
    else:
        object_.cast(property_.murano_class).set_property(
            name=property_.name, value=value, context=context)


@specs.yaql_property(dsl_types.MuranoMethod)
@specs.name('name')
def method_name(murano_method):
    return murano_method.name


@specs.yaql_property(dsl_types.MuranoMethod)
def arguments(murano_method):
    if murano_method.arguments_scheme is None:
        return None
    return tuple(murano_method.arguments_scheme.values())


@specs.yaql_property(dsl_types.MuranoMethod)
@specs.name('declaring_type')
def method_owner(murano_method):
    return murano_method.murano_class


@specs.parameter('method', dsl_types.MuranoMethod)
@specs.parameter('__object', dsl.MuranoObjectParameterType(nullable=True))
@specs.name('invoke')
@specs.method
def method_invoke(context, method, __object, *args, **kwargs):
    executor = helpers.get_executor(context)
    return method.invoke(executor, __object, args, kwargs, context)


@specs.yaql_property(dsl_types.MuranoPackage)
def types(murano_package):
    return tuple(
        murano_package.find_class(cls, False)
        for cls in murano_package.classes
    )


@specs.yaql_property(dsl_types.MuranoPackage)
@specs.name('name')
def package_name(murano_package):
    return murano_package.name


@specs.yaql_property(dsl_types.MuranoPackage)
@specs.name('version')
def package_version(murano_package):
    return murano_package.version


@specs.yaql_property(dsl_types.MuranoMethodArgument)
@specs.name('name')
def argument_name(method_argument):
    return method_argument.name

# TODO(ativelkov): add 'default' to return some wrapped YAQL expression
# @specs.yaql_property(dsl_types.MuranoMethodArgument)
# @specs.name('default')
# def argument_default(method_argument):
#     return method_argument.default


@specs.yaql_property(dsl_types.MuranoMethodArgument)
@specs.name('has_default')
def argument_has_default(method_argument):
    return method_argument.has_default


@specs.yaql_property(dsl_types.MuranoMethodArgument)
@specs.name('declaring_method')
def argument_owner(method_argument):
    return method_argument.murano_method


@specs.yaql_property(dsl_types.MuranoClass)
@specs.name('type')
def type_to_type_ref(murano_class):
    return murano_class.get_reference()


def register(context):
    funcs = (
        class_name, methods, properties, ancestors, package, class_version,
        property_name, property_has_default, property_owner,
        property_usage, property_get_value, property_set_value,
        method_name, arguments, method_owner, method_invoke,
        types, package_name, package_version,
        argument_name, argument_has_default, argument_owner,
        type_to_type_ref
    )
    for f in funcs:
        context.register_function(f)

yaqlization.yaqlize(semantic_version.Version, whitelist=[
    'major', 'minor', 'patch', 'prerelease', 'build'
])
