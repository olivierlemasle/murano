#    Copyright (c) 2014 Mirantis, Inc.
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

import collections
import contextlib
import functools
import itertools
import string
import sys
import uuid

import eventlet.greenpool
import eventlet.greenthread
import semantic_version
import six
from yaql.language import contexts
import yaql.language.exceptions
import yaql.language.expressions
from yaql.language import utils as yaqlutils


from murano.common import utils
from murano.dsl import constants
from murano.dsl import dsl_types
from murano.dsl import exceptions

_threads_sequencer = 0


def evaluate(value, context):
    if isinstance(value, (dsl_types.YaqlExpression,
                          yaql.language.expressions.Statement)):
        return value(context)
    elif isinstance(value, yaqlutils.MappingType):
        return yaqlutils.FrozenDict(
            (evaluate(d_key, context),
             evaluate(d_value, context))
            for d_key, d_value in six.iteritems(value))
    elif yaqlutils.is_sequence(value):
        return tuple(evaluate(t, context) for t in value)
    elif isinstance(value, yaqlutils.SetType):
        return frozenset(evaluate(t, context) for t in value)
    elif yaqlutils.is_iterable(value):
        return tuple(
            evaluate(t, context)
            for t in yaqlutils.limit_iterable(
                value, constants.ITERATORS_LIMIT))
    elif isinstance(value, dsl_types.MuranoObjectInterface):
        return value.object
    else:
        return value


def merge_lists(list1, list2):
    result = []
    for item in list1 + list2:
        exists = False
        for old_item in result:
            if not utils.is_different(item, old_item):
                exists = True
                break
        if not exists:
            result.append(item)
    return result


def merge_dicts(dict1, dict2, max_levels=0):
    result = {}
    for key, value1 in dict1.items():
        result[key] = value1
        if key in dict2:
            value2 = dict2[key]
            if type(value2) != type(value1):
                if ((isinstance(value1,
                                six.string_types) or value1 is None) and
                        (isinstance(value2,
                                    six.string_types) or value2 is None)):
                    continue
                raise TypeError()
            if max_levels != 1 and isinstance(value2, dict):
                result[key] = merge_dicts(
                    value1, value2,
                    0 if max_levels == 0 else max_levels - 1)
            elif max_levels != 1 and isinstance(value2, list):
                result[key] = merge_lists(value1, value2)
            else:
                result[key] = value2
    for key, value1 in dict2.items():
        if key not in result:
            result[key] = value1
    return result


def generate_id():
    return uuid.uuid4().hex


def parallel_select(collection, func, limit=1000):
    # workaround for eventlet issue 232
    # https://github.com/eventlet/eventlet/issues/232
    context = get_context()
    session = get_execution_session()

    def wrapper(element):
        try:
            with contextual(context):
                with execution_session(session):
                    return func(element), False, None
        except Exception as e:
            return e, True, sys.exc_info()[2]

    gpool = eventlet.greenpool.GreenPool(limit)
    result = list(gpool.imap(wrapper, collection))
    try:
        exception = next(t for t in result if t[1])
    except StopIteration:
        return map(lambda t: t[0], result)
    else:
        six.reraise(exception[0], None, exception[2])


def enum(**enums):
    return type('Enum', (), enums)


def get_context():
    current_thread = eventlet.greenthread.getcurrent()
    return getattr(current_thread, constants.TL_CONTEXT, None)


def get_executor(context=None):
    context = context or get_context()
    result = context[constants.CTX_EXECUTOR]
    return None if not result else result()


def get_type(context=None):
    context = context or get_context()
    return context[constants.CTX_TYPE]


def get_execution_session(context=None):
    context = context or get_context()
    session = None
    if context is not None:
        session = context[constants.CTX_EXECUTION_SESSION]
    if session is None:
        current_thread = eventlet.greenthread.getcurrent()
        session = getattr(current_thread, constants.TL_SESSION, None)
    return session


def get_object_store(context=None):
    context = context or get_context()
    this = context[constants.CTX_THIS]
    return this.object_store if isinstance(
        this, dsl_types.MuranoObject) else None


def get_package_loader(context=None):
    context = context or get_context()
    result = context[constants.CTX_PACKAGE_LOADER]
    return None if not result else result()


def get_this(context=None):
    context = context or get_context()
    return context[constants.CTX_THIS]


def get_caller_context(context=None):
    context = context or get_context()
    return context[constants.CTX_CALLER_CONTEXT]


def get_attribute_store(context=None):
    context = context or get_context()
    store = context[constants.CTX_ATTRIBUTE_STORE]
    return None if not store else store()


def get_current_instruction(context=None):
    context = context or get_context()
    return context[constants.CTX_CURRENT_INSTRUCTION]


def get_current_method(context=None):
    context = context or get_context()
    return context[constants.CTX_CURRENT_METHOD]


def get_yaql_engine(context=None):
    context = context or get_context()
    return context[constants.CTX_YAQL_ENGINE]


def get_current_exception(context=None):
    context = context or get_context()
    return context[constants.CTX_CURRENT_EXCEPTION]


def are_property_modifications_allowed(context=None):
    context = context or get_context()
    return context[constants.CTX_ALLOW_PROPERTY_WRITES] or False


def get_class(name, context=None):
    context = context or get_context()
    murano_class = get_type(context)
    return murano_class.package.find_class(name)


def get_current_thread_id():
    global _threads_sequencer

    current_thread = eventlet.greenthread.getcurrent()
    thread_id = getattr(current_thread, constants.TL_ID, None)
    if thread_id is None:
        thread_id = 'T' + str(_threads_sequencer)
        _threads_sequencer += 1
        setattr(current_thread, constants.TL_ID, thread_id)
    return thread_id


@contextlib.contextmanager
def thread_local_attribute(name, value):
    current_thread = eventlet.greenthread.getcurrent()
    old_value = getattr(current_thread, name, None)
    if value is not None:
        setattr(current_thread, name, value)
    elif hasattr(current_thread, name):
        delattr(current_thread, name)
    try:
        yield
    finally:
        if old_value is not None:
            setattr(current_thread, name, old_value)
        elif hasattr(current_thread, name):
            delattr(current_thread, name)


def contextual(ctx):
    return thread_local_attribute(constants.TL_CONTEXT, ctx)


def execution_session(session):
    return thread_local_attribute(constants.TL_SESSION, session)


def parse_version_spec(version_spec):
    if isinstance(version_spec, semantic_version.Spec):
        return normalize_version_spec(version_spec)
    if isinstance(version_spec, semantic_version.Version):
        return normalize_version_spec(
            semantic_version.Spec('==' + str(version_spec)))
    if not version_spec:
        version_spec = '0'
    version_spec = str(version_spec).translate(None, string.whitespace)
    if version_spec[0].isdigit():
        version_spec = '==' + str(version_spec)
    version_spec = semantic_version.Spec(version_spec)
    return normalize_version_spec(version_spec)


def parse_version(version):
    if isinstance(version, semantic_version.Version):
        return version
    if not version:
        version = '0'
    return semantic_version.Version.coerce(str(version))


def traverse(seed, producer=None, track_visited=True):
    if not yaqlutils.is_iterable(seed):
        seed = [seed]
    visited = None if not track_visited else set()
    queue = collections.deque(seed)
    while queue:
        item = queue.popleft()
        if track_visited:
            if item in visited:
                continue
            visited.add(item)
        produced = (yield item)
        if produced is None and producer:
            produced = producer(item)
        if produced:
            queue.extend(produced)


def cast(obj, murano_class, pov_or_version_spec=None):
    if isinstance(obj, dsl_types.MuranoObjectInterface):
        obj = obj.object
    if isinstance(pov_or_version_spec, dsl_types.MuranoClass):
        pov_or_version_spec = pov_or_version_spec.package
    elif isinstance(pov_or_version_spec, six.string_types):
        pov_or_version_spec = parse_version_spec(pov_or_version_spec)

    if isinstance(murano_class, dsl_types.MuranoTypeReference):
        murano_class = murano_class.murano_class
    if isinstance(murano_class, dsl_types.MuranoClass):
        if pov_or_version_spec is None:
            pov_or_version_spec = parse_version_spec(murano_class.version)
        murano_class = murano_class.name

    candidates = []
    for cls in itertools.chain((obj.type,), obj.type.ancestors()):
        if cls.name != murano_class:
            continue
        elif isinstance(pov_or_version_spec, semantic_version.Version):
            if cls.version != pov_or_version_spec:
                continue
        elif isinstance(pov_or_version_spec, semantic_version.Spec):
            if cls.version not in pov_or_version_spec:
                continue
        elif isinstance(pov_or_version_spec, dsl_types.MuranoPackage):
            requirement = pov_or_version_spec.requirements.get(
                cls.package.name)
            if requirement is None:
                raise exceptions.NoClassFound(murano_class)
            if cls.version not in requirement:
                continue
        elif pov_or_version_spec is not None:
            raise ValueError('pov_or_version_spec of unsupported '
                             'type {0}'.format(type(pov_or_version_spec)))
        candidates.append(cls)
    if not candidates:
        raise exceptions.NoClassFound(murano_class)
    elif len(candidates) > 1:
        raise exceptions.AmbiguousClassName(murano_class)
    return obj.cast(candidates[0])


def is_instance_of(obj, class_name, pov_or_version_spec=None):
    try:
        cast(obj, class_name, pov_or_version_spec)
        return True
    except (exceptions.NoClassFound, exceptions.AmbiguousClassName):
        return False


def memoize(func):
    cache = {}
    return get_memoize_func(func, cache)


def get_memoize_func(func, cache):
    @functools.wraps(func)
    def wrap(*args):
        if args not in cache:
            result = func(*args)
            cache[args] = result
            return result
        else:
            return cache[args]
    return wrap


def normalize_version_spec(version_spec):
    def coerce(v):
        return semantic_version.Version('{0}.{1}.{2}'.format(
            v.major, v.minor or 0, v.patch or 0
        ))

    def increment(v):
        # NOTE(ativelkov): replace these implementations with next_minor() and
        # next_major() calls when the semantic_version is updated in global
        # requirements.
        if v.minor is None:
            return semantic_version.Version(
                '.'.join(str(x) for x in [v.major + 1, 0, 0]))
        else:
            return semantic_version.Version(
                '.'.join(str(x) for x in [v.major, v.minor + 1, 0]))

    def extend(v):
        return semantic_version.Version(str(v) + '-0')

    transformations = {
        '>': [('>=', (increment, extend))],
        '>=': [('>=', (coerce,))],
        '<': [('<', (coerce, extend))],
        '<=': [('<', (increment, extend))],
        '!=': [('>=', (increment, extend))],
        '==': [('>=', (coerce,)), ('<', (increment, coerce, extend))]
    }

    new_parts = []
    for item in version_spec.specs:
        if item.kind == '*':
            continue
        elif item.spec.patch is not None:
            new_parts.append(str(item))
        else:
            for op, funcs in transformations[item.kind]:
                new_parts.append('{0}{1}'.format(
                    op,
                    six.moves.reduce(lambda v, f: f(v), funcs, item.spec)
                ))
    if not new_parts:
        return semantic_version.Spec('*')
    return semantic_version.Spec(*new_parts)


semver_to_api_map = {
    '>': 'gt',
    '>=': 'ge',
    '<': 'lt',
    '<=': 'le',
    '!=': 'ne',
    '==': 'eq'
}


def breakdown_spec_to_query(normalized_spec):
    res = []
    for item in normalized_spec.specs:
        if item.kind == '*':
            continue
        else:
            res.append("%s:%s" % (semver_to_api_map[item.kind],
                                  item.spec))
    return res


def link_contexts(parent_context, context):
    if not context:
        return parent_context
    return contexts.LinkedContext(parent_context, context)
