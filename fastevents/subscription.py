from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Iterable, TypeAlias

TagInput: TypeAlias = str | Iterable[str]
SubscriptionClause: TypeAlias = tuple[str, ...]
SubscriptionInput: TypeAlias = str | list[str | SubscriptionClause] | SubscriptionClause


def normalize_tags(tags: TagInput) -> tuple[str, ...]:
    """Normalize tag input into a lowercase, deduplicated, sorted tuple."""

    if isinstance(tags, str):
        raw_tags = [tags]
    else:
        raw_tags = list(tags)

    normalized: set[str] = set()
    for tag in raw_tags:
        value = tag.strip().lower()
        if not value:
            raise ValueError("tags must not contain empty values")
        invalid = [char for char in value if not (char.isalnum() or char in {"_", "."})]
        if invalid:
            raise ValueError(f"invalid tag {tag!r}")
        normalized.add(value)
    return tuple(sorted(normalized))


def normalize_subscription(subscription: SubscriptionInput) -> tuple[SubscriptionClause, ...]:
    """Normalize subscription input into canonical OR-of-AND clauses."""

    if isinstance(subscription, str):
        return ((normalize_pattern(subscription),),)

    if isinstance(subscription, tuple):
        return (tuple(normalize_pattern(item) for item in subscription),)

    clauses: list[SubscriptionClause] = []
    for clause in subscription:
        if isinstance(clause, tuple):
            clauses.append(tuple(normalize_pattern(item) for item in clause))
        else:
            clauses.append((normalize_pattern(clause),))
    return tuple(clauses)


def normalize_pattern(pattern: str) -> str:
    """Normalize one subscription pattern, including negative matches."""

    value = pattern.strip().lower()
    if not value:
        raise ValueError("subscription pattern must not be empty")
    negative = value.startswith("-")
    subject = value[1:] if negative else value
    invalid = [char for char in subject if not (char.isalnum() or char in {"_", ".", "*"})]
    if invalid:
        raise ValueError(f"invalid subscription pattern {pattern!r}")
    if not subject:
        raise ValueError("negative pattern must include a subject")
    return f"-{subject}" if negative else subject


def matches_subscription(subscription: tuple[SubscriptionClause, ...], tags: tuple[str, ...]) -> bool:
    """Return true when any subscription clause matches the event tags."""

    return any(matches_clause(clause, tags) for clause in subscription)


def matches_clause(clause: SubscriptionClause, tags: tuple[str, ...]) -> bool:
    """Return true when all patterns in one clause match the event tags."""

    return all(matches_pattern(pattern, tags) for pattern in clause)


def matches_pattern(pattern: str, tags: tuple[str, ...]) -> bool:
    """Match one normalized pattern against the event tag tuple."""

    if pattern.startswith("-"):
        return not any(fnmatchcase(tag, pattern[1:]) for tag in tags)
    return any(fnmatchcase(tag, pattern) for tag in tags)
