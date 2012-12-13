from pypy.rlib.rstring import StringBuilder
from pypy.rlib.rsre.rsre_core import (OPCODE_LITERAL, OPCODE_LITERAL_IGNORE,
    OPCODE_SUCCESS, OPCODE_ASSERT, OPCODE_MARK, OPCODE_REPEAT, OPCODE_ANY,
    OPCODE_ANY_ALL, OPCODE_MAX_UNTIL, OPCODE_MIN_UNTIL, OPCODE_GROUPREF,
    OPCODE_AT, OPCODE_BRANCH, OPCODE_RANGE, OPCODE_JUMP, OPCODE_ASSERT_NOT,
    OPCODE_CATEGORY, OPCODE_FAILURE, OPCODE_IN, OPCODE_NEGATE)


IGNORE_CASE = 1 << 0
DOT_ALL = 1 << 1
MULTI_LINE = 1 << 2
WORD = 1 << 3

SPECIAL_CHARS = "()|?*+{^$.[\\#"

HEX_ESCAPES = {
    "x": 2,
    "u": 4,
    "U": 8
}
CHARACTER_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}

MAX_REPEAT = 65535

AT_BEGINNING = 0
AT_BEGINNING_LINE = 1
AT_BEGINNING_STRING = 2
AT_BOUNDARY = 3
AT_NON_BOUNDARY = 4
AT_END = 5
AT_END_LINE = 6
AT_END_STRING = 7
AT_LOC_BOUNDARY = 8
AT_LOC_NON_BOUNDARY = 9
AT_UNI_BOUNDARY = 10
AT_UNI_NON_BOUNDARY = 11

CATEGORY_DIGIT = 0
CATEGORY_NOT_DIGIT = 1
CATEGORY_SPACE = 2
CATEGORY_NOT_SPACE = 3
CATEGORY_WORD = 4
CATEGORY_NOT_WORD = 5
CATEGORY_LINEBREAK = 6
CATEGORY_NOT_LINEBREAK = 7
CATEGORY_LOC_WORD = 8
CATEGORY_LOC_NOT_WORD = 9
CATEGORY_UNI_DIGIT = 10
CATEGORY_UNI_NOT_DIGIT = 11
CATEGORY_UNI_SPACE = 12
CATEGORY_UNI_NOT_SPACE = 13
CATEGORY_UNI_WORD = 14
CATEGORY_UNI_NOT_WORD = 15
CATEGORY_UNI_LINEBREAK = 16
CATEGORY_UNI_NOT_LINEBREAK = 17


class UnscopedFlagSet(Exception):
    def __init__(self, global_flags):
        Exception.__init__(self)
        self.global_flags = global_flags


class FirstSetError(Exception):
    pass


class RegexpError(Exception):
    pass


class ParseError(Exception):
    pass


class Source(object):
    def __init__(self, s):
        self.pos = 0
        self.s = s

        self.ignore_space = False

    def at_end(self):
        s = self.s
        pos = self.pos

        if self.ignore_space:
            while True:
                if s[pos].isspace():
                    pos += 1
                elif s[pos] == "#":
                    pos = s.index("\n", pos)
                else:
                    break
        return pos >= len(s)

    def get(self):
        s = self.s
        pos = self.pos
        if self.ignore_space:
            while True:
                if s[pos].isspace():
                    pos += 1
                elif s[pos] == "#":
                    pos = s.index("\n", pos)
                else:
                    break
        try:
            ch = s[pos]
            self.pos = pos + 1
            return ch
        except IndexError:
            self.pos = pos
            return ""
        except ValueError:
            self.pos = len(s)
            return ""

    def match(self, substr):
        s = self.s
        pos = self.pos

        if self.ignore_space:
            for c in substr:
                while True:
                    if s[pos].isspace():
                        pos += 1
                    elif s[pos] == "#":
                        pos = s.index("\n", pos)
                    else:
                        break

                if s[pos] != c:
                    return False
                pos += 1
            self.pos = pos
            return True
        else:
            if not s.startswith(substr, pos):
                return False
            self.pos = pos + len(substr)
            return True

    def expect(self, substr):
        if not self.match(substr):
            raise RegexpError("Missing %s" % substr)


class Info(object):
    OPEN = 0
    CLOSED = 1

    def __init__(self, flags):
        self.flags = flags

        self.group_count = 0
        self.used_groups = {}
        self.group_state = {}
        self.group_index = {}
        self.group_name = {}
        self.named_lists_used = {}
        self.defined_groups = {}

    def new_group(self, name=None):
        if name in self.group_index:
            if self.group_index[name] in self.used_groups:
                raise RegexpError("duplicate group")
        else:
            while True:
                self.group_count += 1
                if name is None or self.group_count not in self.group_name:
                    break
            group = self.group_count
            if name is not None:
                self.group_index[name] = group
                self.group_name[group] = name
        self.used_groups[group] = None
        self.group_state[group] = self.OPEN
        return group

    def close_group(self, group):
        self.group_state[group] = self.CLOSED

    def normalize_group(self, name):
        if name.isdigit():
            return int(name)
        else:
            return self.group_index[name]

    def is_open_group(self, name):
        group = self.normalize_group(name)
        return group in self.group_state and self.group_state[group] == self.OPEN



class CompilerContext(object):
    def __init__(self):
        self.data = []

    def emit(self, opcode):
        self.data.append(opcode)

    def tell(self):
        return len(self.data)

    def patch(self, pos, value):
        self.data[pos] = value

    def build(self):
        return self.data


class Counts(object):
    def __init__(self, min_count, max_count=MAX_REPEAT):
        self.min_count = min_count
        self.max_count = max_count


class RegexpBase(object):
    _attrs_ = ["positive", "case_insensitive", "zerowidth"]

    def __init__(self, positive=True, case_insensitive=False, zerowidth=False):
        self.positive = positive
        self.case_insensitive = case_insensitive
        self.zerowidth = zerowidth

    def with_flags(self, positive=None, case_insensitive=None, zerowidth=None):
        positive = positive if positive is not None else self.positive
        case_insensitive = case_insensitive if case_insensitive is not None else self.case_insensitive
        zerowidth = zerowidth if zerowidth is not None else self.zerowidth
        if (positive == self.positive and
            case_insensitive == self.case_insensitive and
            zerowidth == self.zerowidth):
            return self
        return self.rebuild(positive, case_insensitive, zerowidth)


class Character(RegexpBase):
    def __init__(self, value, case_insensitive=False, positive=True, zerowidth=False):
        RegexpBase.__init__(self, case_insensitive=case_insensitive, positive=positive, zerowidth=zerowidth)
        self.value = value

    def rebuild(self, positive, case_insensitive, zerowidth):
        return Character(self.value, positive=positive, case_insensitive=case_insensitive, zerowidth=zerowidth)

    def fix_groups(self):
        pass

    def optimize(self, info, in_set=False):
        return self

    def has_simple_start(self):
        return True

    def can_be_affix(self):
        return True

    def get_firstset(self):
        return {self: None}

    def is_empty(self):
        return False

    def compile(self, ctx):
        ctx.emit(OPCODE_LITERAL_IGNORE if self.case_insensitive else OPCODE_LITERAL)
        ctx.emit(self.value)


class Any(RegexpBase):
    def is_empty(self):
        return False

    def fix_groups(self):
        pass

    def optimize(self, info):
        return self

    def has_simple_start(self):
        return True

    def get_firstset(self):
        raise FirstSetError

    def compile(self, ctx):
        ctx.emit(OPCODE_ANY)


class AnyAll(RegexpBase):
    def fix_groups(self):
        pass

    def optimize(self, info):
        return self

    def has_simple_start(self):
        return True

    def compile(self, ctx):
        ctx.emit(OPCODE_ANY_ALL)


class ZeroWidthBase(RegexpBase):
    def fix_groups(self):
        pass

    def optimize(self, info, in_set=False):
        return self

    def has_simple_start(self):
        return False

    def get_firstset(self):
        return {None: None}


class StartOfString(ZeroWidthBase):
    def compile(self, ctx):
        ctx.emit(OPCODE_AT)
        ctx.emit(AT_BEGINNING_STRING)


class EndOfString(ZeroWidthBase):
    def compile(self, ctx):
        ctx.emit(OPCODE_AT)
        ctx.emit(AT_END_STRING)


class Property(RegexpBase):
    def __init__(self, value, positive=True, case_insensitive=False, zerowidth=False):
        RegexpBase.__init__(self, positive=positive, case_insensitive=case_insensitive, zerowidth=zerowidth)
        self.value = value

    def rebuild(self, positive, case_insensitive, zerowidth):
        return Property(self.value, positive, case_insensitive, zerowidth)

    def getwidth(self):
        return 1, 1

    def is_empty(self):
        return False

    def get_firstset(self):
        return {self: None}

    def fix_groups(self):
        pass

    def optimize(self, info, in_set=False):
        return self

    def compile(self, ctx):
        ctx.emit(OPCODE_CATEGORY)
        ctx.emit(self.value)


class Range(RegexpBase):
    def __init__(self, lower, upper, positive=True, case_insensitive=False, zerowidth=False):
        RegexpBase.__init__(self, positive=positive, case_insensitive=case_insensitive, zerowidth=zerowidth)
        self.lower = lower
        self.upper = upper

    def rebuild(self, positive, case_insensitive, zerowidth):
        return Range(self.lower, self.upper, positive, case_insensitive, zerowidth)

    def fix_groups(self):
        pass

    def has_simple_start(self):
        return False

    def get_firstset(self):
        raise FirstSetError

    def optimize(self, info, in_set=False):
        return self

    def can_be_affix(self):
        return True

    def compile(self, ctx):
        if not self.positive:
            ctx.emit(OPCODE_NEGATE)
        ctx.emit(OPCODE_RANGE)
        ctx.emit(self.lower)
        ctx.emit(self.upper)


class Sequence(RegexpBase):
    def __init__(self, items):
        RegexpBase.__init__(self)
        self.items = items

    def fix_groups(self):
        for item in self.items:
            item.fix_groups()

    def optimize(self, info):
        items = []
        for item in self.items:
            item = item.optimize(info)
            if isinstance(item, Sequence):
                items.extend(item.items)
            else:
                items.append(item)
        return make_sequence(items)

    def has_simple_start(self):
        return self.items and self.items[0].has_simple_start()

    def get_firstset(self):
        fs = {}
        for item in self.items:
            fs.update(item.get_firstset())
            if None not in fs:
                return fs
            del fs[None]
        fs[None] = None
        return fs

    def compile(self, ctx):
        for item in self.items:
            item.compile(ctx)


class Branch(RegexpBase):
    def __init__(self, branches):
        RegexpBase.__init__(self)
        self.branches = branches

    def fix_groups(self):
        for b in self.branches:
            b.fix_groups()

    def _flatten_branches(self, info, branches):
        new_branches = []
        for b in branches:
            b = b.optimize(info)
            if isinstance(b, Branch):
                new_branches.extend(b.branches)
            else:
                new_branches.append(b)
        return new_branches

    def _split_common_prefix(self, info, branches):
        alternatives = []
        for b in branches:
            if isinstance(b, Sequence):
                alternatives.append(b.items)
            else:
                alternatives.append([b])
        max_count = min([len(a) for a in alternatives])
        prefix = alternatives[0]
        pos = 0
        end_pos = max_count
        while (pos < end_pos and prefix[pos].can_be_affix() and
            all([a[pos] == prefix[pos] for a in alternatives])):
            pos += 1
        if pos == 0:
            return [], branches
        new_branches = []
        for a in alternatives:
            new_branches.append(make_sequence(a[pos:]))
        return prefix[:pos], new_branches

    def _split_common_suffix(self, info, branches):
        alternatives = []
        for b in branches:
            if isinstance(b, Sequence):
                alternatives.append(b.items)
            else:
                alternatives.append([b])
        max_count = min([len(a) for a in alternatives])
        suffix = alternatives[0]
        pos = -1
        end_pos = -1 - max_count
        while (pos > end_pos and suffix[pos].can_be_affix() and
            all([a[pos] == suffix[pos] for a in alternatives])):
            pos -= 1
        count = -1 - pos
        if count == 0:
            return [], branches
        new_branches = []
        for a in alternatives:
            new_branches.append(make_sequence(a[:-count]))
        return suffix[-count:], new_branches

    def _is_simple_character(self, c):
        return isinstance(c, Character) and c.positive and not c.case_insensitive

    def _flush_char_prefix(self, info, prefixed, order, new_branches):
        if not prefixed:
            return
        for value, branches in sorted(prefixed.items(), key=lambda pair: order[pair[0]]):
            if len(branches) == 1:
                new_branches.append(make_sequence(branches[0]))
            else:
                subbranches = []
                optional = False
                for b in branches:
                    if len(b) > 1:
                        subbranches.append(make_sequence(b[1:]))
                    elif not optional:
                        subbranches.append(Sequence())
                        optional = True
                sequence = Sequence([Character(value), Branch(subbranches)])
                new_branches.append(sequence.optimize(info))
        prefixed.clear()
        order.clear()

    def _merge_common_prefixes(self, info, branches):
        prefixed = {}
        order = {}
        new_branches = []
        for b in branches:
            if self._is_simple_character(b):
                prefixed.setdefault(b.value, []).append([b])
                order.setdefault(b.value, len(order))
            elif isinstance(b, Sequence) and b.items and self._is_simple_character(b.items[0]):
                prefixed.setdefault(b.items[0].value, []).append(b.items)
                order.setdefault(b.items[0].value, len(order))
            else:
                self._flush_char_prefix(info, prefixed, order, new_branches)
                new_branches.append(b)
        self._flush_char_prefix(info, prefixed, order, new_branches)
        return new_branches

    def _flush_set_members(self, info, items, case_insensitive, new_branches):
        if not items:
            return
        if len(items) == 1:
            item = list(items)[0]
        else:
            item = SetUnion(info, list(items)).optimize(info)
        new_branches.append(item.with_flags(case_insensitive=case_insensitive))
        items.clear()

    def _reduce_to_set(self, info, branches):
        new_branches = []
        items = {}
        case_insensitive = False
        for b in branches:
            if isinstance(b, (Character, Property, SetBase)):
                if b.case_insensitive != case_insensitive:
                    self._flush_set_members(info, items, case_insensitive, new_branches)
                    case_insensitive = b.case_insensitive
                items[b.with_flags(case_insensitive=False)] = False
            else:
                self._flush_set_members(info, items, case_insensitive, new_branches)
                new_branches.append(b)
        self._flush_set_members(info, items, case_insensitive, new_branches)
        return new_branches

    def optimize(self, info):
        branches = self._flatten_branches(info, self.branches)

        prefix, branches = self._split_common_prefix(info, branches)
        suffix, branches = self._split_common_suffix(info, branches)

        branches = self._merge_common_prefixes(info, branches)
        branches = self._reduce_to_set(info, branches)
        if len(branches) > 1:
            sequence = prefix + [Branch(branches)] + suffix
        else:
            sequence = prefix + branches + suffix
        return make_sequence(sequence)

    def has_simple_start(self):
        return False

    def get_firstset(self):
        fs = {}
        for b in self.branches:
            fs.update(b.get_firstset())
        return fs or {None: None}

    def compile(self, ctx):
        ctx.emit(OPCODE_BRANCH)
        tail = []
        for b in self.branches:
            pos = ctx.tell()
            ctx.emit(0)
            b.compile(ctx)
            ctx.emit(OPCODE_JUMP)
            tail.append(ctx.tell())
            ctx.emit(0)
            ctx.patch(pos, ctx.tell() - pos)
        ctx.emit(0)
        for t in tail:
            ctx.patch(t, ctx.tell() - t)


class BaseRepeat(RegexpBase):
    def __init__(self, subpattern, min_count, max_count):
        RegexpBase.__init__(self)
        self.subpattern = subpattern
        self.min_count = min_count
        self.max_count = max_count

    def fix_groups(self):
        self.subpattern.fix_groups()

    def is_empty(self):
        return self.subpattern.is_empty()

    def has_simple_start(self):
        return False

    def get_firstset(self):
        fs = self.subpattern.get_firstset()
        if self.min_count == 0:
            fs[None] = None
        return fs

    def compile(self, ctx):
        ctx.emit(OPCODE_REPEAT)
        pos = ctx.tell()
        ctx.emit(0)
        ctx.emit(self.min_count)
        ctx.emit(self.max_count)
        self.subpattern.compile(ctx)
        ctx.patch(pos, ctx.tell() - pos)
        ctx.emit(self.UNTIL_OPCODE)


class GreedyRepeat(BaseRepeat):
    UNTIL_OPCODE = OPCODE_MAX_UNTIL

    def optimize(self, info):
        subpattern = self.subpattern.optimize(info)
        return GreedyRepeat(subpattern, self.min_count, self.max_count)


class LazyRepeat(BaseRepeat):
    UNTIL_OPCODE = OPCODE_MIN_UNTIL

    def optimize(self, info):
        subpattern = self.subpattern.optimize(info)
        return LazyRepeat(subpattern, self.min_count, self.max_count)


class LookAround(RegexpBase):
    def __init__(self, subpattern, behind, positive):
        RegexpBase.__init__(self, positive=positive)
        self.subpattern = subpattern
        self.behind = behind

    def fix_groups(self):
        self.subpattern.fix_groups()

    def has_simple_start(self):
        return False

    def get_firstset(self):
        raise FirstSetError

    def optimize(self, info):
        return LookAround(self.subpattern.optimize(info), self.behind, self.positive)

    def compile(self, ctx):
        ctx.emit(OPCODE_ASSERT if self.positive else OPCODE_ASSERT_NOT)
        pos = ctx.tell()
        ctx.emit(0)
        if self.behind:
            lo, hi = self.subpattern.getwidth()
            if lo != hi:
                raise RegexpError("look-behind requires fixed-width pattern")
            ctx.emit(lo)
        else:
            ctx.emit(0)
        self.subpattern.compile(ctx)
        ctx.emit(OPCODE_SUCCESS)
        ctx.patch(pos, ctx.tell() - pos)


class Group(RegexpBase):
    def __init__(self, info, group, subpattern):
        RegexpBase.__init__(self)
        self.info = info
        self.group = group
        self.subpattern = subpattern

    def fix_groups(self):
        self.info.defined_groups[self.group] = self
        self.subpattern.fix_groups()

    def can_be_affix(self):
        return False

    def optimize(self, info):
        return Group(self.info, self.group, self.subpattern.optimize(info))

    def is_empty(self):
        return False

    def has_simple_start(self):
        return self.subpattern.has_simple_start()

    def get_firstset(self):
        return self.subpattern.get_firstset()

    def compile(self, ctx):
        ctx.emit(OPCODE_MARK)
        ctx.emit((self.group - 1) * 2)
        self.subpattern.compile(ctx)
        ctx.emit(OPCODE_MARK)
        ctx.emit((self.group - 1) * 2 + 1)


class RefGroup(RegexpBase):
    def __init__(self, info, group, case_insensitive=False):
        RegexpBase.__init__(self, case_insensitive=case_insensitive)
        self.info = info
        self.group = group

    def fix_groups(self):
        if not 1 <= self.group <= self.info.group_count:
            raise RegexpError("unknown group")

    def optimize(self, info):
        return self

    def compile(self, ctx):
        assert not self.case_insensitive
        ctx.emit(OPCODE_GROUPREF)
        ctx.emit(self.group - 1)


class SetBase(RegexpBase):
    def __init__(self, info, items, positive=True, case_insensitive=False, zerowidth=False):
        RegexpBase.__init__(self, zerowidth=zerowidth)
        self.info = info
        self.items = items

    def is_empty(self):
        return False

    def can_be_affix(self):
        return True

    def fix_groups(self):
        pass

    def has_simple_start(self):
        return True

    def get_firstset(self):
        return {self: None}


class SetUnion(SetBase):
    def optimize(self, info, in_set=False):
        items = []
        for item in self.items:
            item = item.optimize(info, in_set=True)
            if isinstance(item, SetUnion) and item.positive:
                items.extend(item.items)
            else:
                items.append(item)
        if len(items) == 1:
            return items[0].with_flags(
                positive=item.positive == self.positive,
                case_insensitive=self.case_insensitive,
                zerowidth=self.zerowidth
            ).optimize(info, in_set=in_set)
        return SetUnion(self.info, items)

    def rebuild(self, positive, case_insensitive, zerowidth):
        return SetUnion(self.info, self.items, positive, case_insensitive, zerowidth).optimize(self.info)

    def compile(self, ctx):
        ctx.emit(OPCODE_IN)
        pos = ctx.tell()
        ctx.emit(0)
        for item in self.items:
            item.compile(ctx)
        ctx.emit(OPCODE_FAILURE)
        ctx.patch(pos, ctx.tell() - pos)


class SetIntersection(SetBase):
    def rebuild(self, positive, case_insensitive, zerowidth):
        return SetIntersection(self.info, self.items, positive=positive, case_insensitive=case_insensitive, zerowidth=zerowidth)

    def optimize(self, info, in_set=False):
        items = []
        for item in self.items:
            item = item.optimize(info, in_set=True)
            if isinstance(item, SetIntersection) and item.positive:
                items.extend(item.items)
            else:
                items.append(item)
        if len(items) == 1:
            return items[0].with_flags(
                case_insensitive=self.case_insensitive,
                zerowidth=self.zerowidth,
            ).optimize(info, in_set)
        return SetIntersection(info, items)

    def compile(self, ctx):
        Sequence([
            LookAround(SetUnion(self.info, [item]), behind=False, positive=True)
            for item in self.items[:-1]
        ] + [SetUnion(self.info, [self.items[-1]])]).compile(ctx)


POSITION_ESCAPES = {}
CHARSET_ESCAPES = {
    "d": Property(CATEGORY_DIGIT),
}


def make_character(info, value, in_set=False):
    if in_set:
        return Character(value)
    return Character(value, case_insensitive=info.flags & IGNORE_CASE)


def make_sequence(items):
    if len(items) == 1:
        return items[0]
    return Sequence(items)


def make_atomic(info, subpattern):
    group = info.new_group()
    info.close_group(group)
    return Sequence([
        LookAround(Group(info, group, subpattern), behind=False, positive=True),
        RefGroup(info, group),
    ])


def make_ref_group(info, name):
    return RefGroup(info, name, case_insensitive=info.flags & IGNORE_CASE)


def _parse_pattern(source, info):
    previous_groups = info.used_groups.copy()
    branches = [_parse_sequence(source, info)]
    all_groups = info.used_groups
    while source.match("|"):
        info.used_groups = previous_groups.copy()
        branches.append(_parse_sequence(source, info))
        all_groups.update(info.used_groups)
    info.used_groups = all_groups

    if len(branches) == 1:
        return branches[0]
    return Branch(branches)


def _parse_sequence(source, info):
    sequence = []
    item = _parse_item(source, info)
    while item:
        sequence.append(item)
        item = _parse_item(source, info)

    return make_sequence(sequence)


def _parse_item(source, info):
    element = _parse_element(source, info)
    counts = _parse_quantifier(source, info)
    if counts is not None:
        min_count, max_count = counts.min_count, counts.max_count

        if element.is_empty() or min_count == max_count == 1:
            return element

        if source.match("?"):
            return LazyRepeat(element, min_count, max_count)
        elif source.match("+"):
            return make_atomic(info, GreedyRepeat(element, min_count, max_count))
        else:
            return GreedyRepeat(element, min_count, max_count)
    return element


def _parse_element(source, info):
    here = source.pos
    ch = source.get()
    if ch in SPECIAL_CHARS:
        if ch in ")|":
            source.pos = here
            return None
        elif ch == "\\":
            return _parse_escape(source, info, in_set=False)
        elif ch == "(":
            element = _parse_paren(source, info)
            if element is not None:
                return element
        elif ch == ".":
            if info.flags & DOT_ALL:
                return AnyAll()
            else:
                return Any()
        elif ch == "[":
            return _parse_set(source, info)
        elif ch == "^":
            return StartOfString()
        elif ch == "$":
            return EndOfString()
        elif ch == "{":
            here2 = source.pos
            counts = _parse_quantifier(source, info)
            if counts is not None:
                raise RegexpError("nothing to repeat")
            source.pos = here2
            return make_character(info, ord(ch))
        elif ch in "?*+":
            raise RegexpError("nothing to repeat")
        else:
            return make_character(info, ord(ch))
    else:
        return make_character(info, ord(ch))


def _parse_quantifier(source, info):
    while True:
        here = source.pos
        if source.match("?"):
            return Counts(0, 1)
        elif source.match("*"):
            return Counts(0)
        elif source.match("+"):
            return Counts(1)
        elif source.match("{"):
            try:
                return _parse_limited_quantifier(source)
            except ParseError:
                pass
        elif source.match("(?#"):
            _parse_comment(source)
            continue
        break
    source.pos = here
    return None


def _parse_paren(source, info):
    if source.match("?"):
        if source.match("<"):
            if source.match("="):
                return _parse_lookaround(source, info, behind=True, positive=True)
            elif source.match("!"):
                return _parse_lookaround(source, info, behind=True, positive=False)
            name = _parse_name(source)
            group = info.new_group(name)
            source.expect(">")
            saved_flags = info.flags
            saved_ignore = source.ignore_space
            try:
                subpattern = _parse_pattern(source, info)
            finally:
                source.ignore_space = saved_ignore
                info.flags = saved_flags
            source.expect(")")
            info.close_group(group)
            return Group(info, group, subpattern)
        elif source.match("="):
            return _parse_lookaround(source, info, behind=False, positive=True)
        elif source.match("!"):
            return _parse_lookaround(source, info, behind=False, positive=False)
        elif source.match("#"):
            _parse_comment(source)
            return
        elif source.match("("):
            return _parse_conditional(source, info)
        elif source.match(">"):
            return _parse_atomic(source, info)
        elif source.match("|"):
            return _parse_common(source, info)
        else:
            here = source.pos
            ch = source.get()
            if ch == "R" or "0" <= ch <= "9":
                return _parse_call_group(source, info, ch)
            elif ch == "&":
                return _parse_call_named_group(source, info)
            else:
                source.pos = here
                return _parse_flags_subpattern(source, info)
    group = info.new_group()
    saved_flags = info.flags
    saved_ignore = source.ignore_space
    try:
        subpattern = _parse_pattern(source, info)
    finally:
        source.ignore_space = saved_ignore
        info.flags = saved_flags
    source.expect(")")
    info.close_group(group)
    return Group(info, group, subpattern)


def _parse_atomic(source, info):
    saved_flags = info.flags
    saved_ignore = source.ignore_space
    try:
        subpattern = _parse_pattern(source, info)
    finally:
        source.ignore_space = saved_ignore
        info.flags = saved_flags
    source.expect(")")
    return make_atomic(info, subpattern)


def _parse_set(source, info):
    saved_ignore = source.ignore_space
    source.ignore_space = False
    negate = source.match("^")
    try:
        item = _parse_set_intersect(source, info)
        source.expect("]")
    finally:
        source.ignore_space = saved_ignore

    if negate:
        items = item.with_flags(positive=not item.positive)
    return item.with_flags(case_insensitive=info.flags & IGNORE_CASE)


def _parse_set_intersect(source, info):
    items = [_parse_set_implicit_union(source, info)]
    while source.match("&&"):
        items.append(_parse_set_implicit_union(source, info))

    if len(items) == 1:
        return items[0]
    return SetIntersection(info, items)


def _parse_set_implicit_union(source, info):
    items = [_parse_set_member(source, info)]
    while True:
        here = source.pos
        if source.match("]"):
            source.pos = here
            break
        if source.match("&&"):
            source.pos = here
            break
        items.append(_parse_set_member(source, info))
    if len(items) == 1:
        return items[0]
    return SetUnion(info, items)


def _parse_set_member(source, info):
    start = _parse_set_item(source, info)
    if (not isinstance(start, Character) or not start.positive or
        not source.match("-")):
        return start

    here = source.pos
    if source.match("]"):
        source.pos = here
        return SetUnion(info, [start, Character(ord("-"))])
    end = _parse_set_item(source, info)
    if not isinstance(end, Character) or not end.positive:
        return SetUnion(info, [start, Character(ord("-")), end])
    if start.value > end.value:
        raise RegexpError("bad character range")
    if start.value == end.value:
        return start
    return Range(start.value, end.value)


def _parse_set_item(source, info):
    if source.match("\\"):
        return _parse_escape(source, info, in_set=True)

    here = source.pos
    if source.match("[:"):
        try:
            return _parse_posix_class(source, info)
        except ParseError:
            source.pos = here
    if source.match("["):
        negate = source.match("^")
        item = _parse_set_intersect(source, info)
        source.expect("]")
        if negate:
            item = item.with_flags(positive=not item.positive)
        return item
    ch = source.get()
    if not ch:
        raise RegexpError("bad set")
    return Character(ord(ch))


def _parse_escape(source, info, in_set):
    saved_ignore = source.ignore_space
    source.ignore_space = False
    ch = source.get()
    source.ignore_space = saved_ignore
    if not ch:
        raise RegexpError("bad escape")
    if ch in HEX_ESCAPES:
        return _parse_hex_escape(source, info, HEX_ESCAPES[ch], in_set)
    elif ch == "g" and not in_set:
        here = source.pos
        try:
            return _parse_group_ref(source, info)
        except RegexpError:
            source.pos = here
        return make_character(info, ord(ch), in_set)
    elif ch == "G" and not in_set:
        return SearchAncor()
    elif ch == "L" and not in_set:
        return _parse_string_set(source, info)
    elif ch == "N":
        return _parse_named_char(source, info, in_set)
    elif ch in "pP":
        return _parse_property(source, info, ch == "p", in_set)
    elif ch == "X" and not in_set:
        return Grapheme()
    elif ch.isalpha():
        if not in_set:
            if info.flags & WORD:
                value = WORD_POSITION_ESCAPES.get(ch)
            else:
                value = POSITION_ESCAPES.get(ch)
            if value is not None:
                return value
        value = CHARSET_ESCAPES.get(ch)
        if value is not None:
            return value
        value = CHARACTER_ESCAPES.get(ch)
        if value is not None:
            return Character(ord(value))
        return make_character(info, ord(ch), in_set)
    elif ch.isdigit():
        return _parse_numeric_escape(source, info, ch, in_set)
    else:
        return make_character(info, ord(ch), in_set)


def _parse_lookaround(source, info, behind, positive):
    saved_flags = info.flags
    saved_ignore = source.ignore_space
    try:
        subpattern = _parse_pattern(source, info)
    finally:
        source.ignore_space = saved_ignore
        info.flags = saved_flags
    source.expect(")")
    return LookAround(subpattern, behind=behind, positive=positive)


def _parse_limited_quantifier(source):
    min_count = _parse_count(source)
    ch = source.get()
    if ch == ",":
        max_count = _parse_count(source)
        if not source.match("}"):
            raise ParseError
        min_count = int(min_count) if min_count else 0
        max_count = int(max_count) if max_count else MAX_REPEAT
        if min_count > max_count:
            raise RegexpError("min repeat gereater than max repeat")
        if max_count > MAX_REPEAT:
            raise RegexpError("repeat count too big")
        return Counts(min_count, max_count)
    if ch != "}":
        raise ParseError
    if not min_count:
        raise ParseError
    min_count = int(min_count)
    if min_count > MAX_REPEAT:
        raise RegexpError("repeat count too big")
    return Counts(min_count, min_count)


def _parse_count(source):
    b = StringBuilder(2)
    while True:
        here = source.pos
        ch = source.get()
        if ch.isdigit():
            b.append(ch)
        else:
            source.pos = here
            break
    return int(b.build())


def _parse_comment(source):
    while True:
        ch = source.get()
        if ch == ")":
            break
        elif not ch:
            break


def _parse_name(source):
    b = StringBuilder(5)
    while True:
        here = source.pos
        ch = source.get()
        if ch in ")>":
            source.pos = here
            break
        elif not ch:
            break
        else:
            b.append(ch)
    return b.build()


def _parse_group_ref(source, info):
    source.expect("<")
    name = _parse_name(source)
    source.expect(">")
    if info.is_open_group(name):
        raise RegexpError("can't refer to an open group")
    return make_ref_group(info, info.normalize_group(name))


def _compile_firstset(info, fs):
    if not fs or None in fs:
        return []
    members = {}
    for i in fs:
        if i.case_insensitive:
            if isinstance(i, Character):
                if _is_cased(info, i.value):
                    return []
            elif isinstance(i, SetBase):
                return []
        members[i.with_flags(case_insensitive=False)] = None
    fs = SetUnion(info, list(members), zerowidth=True)
    fs = fs.optimize(info, in_set=True)
    ctx = CompilerContext()
    fs.compile(ctx)
    return ctx.build()


def compile(pattern, flags=0):
    global_flags = flags
    while True:
        source = Source(pattern)
        info = Info(flags)
        try:
            parsed = _parse_pattern(source, info)
        except UnscopedFlagSet as e:
            global_flags = e.flags | flags
        else:
            break

    if not source.at_end():
        raise RegexpError("trailing characters in pattern")

    parsed.fix_groups()
    parsed = parsed.optimize(info)

    # regex.py:510
    assert not info.named_lists_used

    ctx = CompilerContext()
    parsed.compile(ctx)
    ctx.emit(OPCODE_SUCCESS)
    code = ctx.build()

    if not parsed.has_simple_start():
        # Get the first set, if possible.
        try:
            fs_code = _compile_firstset(info, parsed.get_firstset())
            code = fs_code + code
        except FirstSetError:
            pass

    index_group = dict([(v, n) for n, v in info.group_index.iteritems()])
    return code, info.flags, info.group_count, info.group_index, index_group
