"""Architecture-aware classification of vector/SIMD register names.

GDB reports register names through MI but not their kind.  One global name
pattern cannot work because the same name means different things on different
targets: RISC-V ``s0`` and MIPS ``s0`` are integer registers, while ARM ``s0``
is a FP/SIMD view.  The family is therefore inferred from names that are unique
to one target, and every family carries exact name ranges.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

RegisterFamily = Literal[
    "x86",
    "aarch64",
    "arm",
    "riscv",
    "powerpc",
    "s390",
    "loongarch",
    "mips",
]


@dataclass(frozen=True)
class _VectorRegisters:
    """Names that belong to the vector/SIMD register file of one family."""

    ranges: tuple[tuple[str, int, int], ...] = ()
    exact: frozenset[str] = frozenset()
    patterns: tuple[re.Pattern[str], ...] = ()


# SME tile and tile-slice pseudo-registers, for example ``za0b`` and ``za0hb0``.
_AARCH64_ZA_RE = re.compile(r"^za\d+(?:[bhsdq]|[hv][bhsdq]\d+)$")

# The low halves of the s390 vector registers, when a target description lists
# them separately from the combined ``v0``-``v31`` registers.
_S390_VECTOR_LOW_RE = re.compile(r"^v(?:[0-9]|1[0-5])l$")


_FAMILY_VECTOR_REGISTERS: dict[RegisterFamily, _VectorRegisters] = {
    "x86": _VectorRegisters(
        ranges=(
            ("xmm", 0, 31),
            ("ymm", 0, 31),
            ("zmm", 0, 31),
            ("mm", 0, 7),
            ("k", 0, 7),
            ("tmm", 0, 7),
        )
    ),
    "aarch64": _VectorRegisters(
        ranges=(
            ("v", 0, 31),
            ("q", 0, 31),
            ("d", 0, 31),
            ("s", 0, 31),
            ("h", 0, 31),
            ("b", 0, 31),
            ("z", 0, 31),
            ("p", 0, 15),
        ),
        exact=frozenset({"ffr", "za", "zt0"}),
        patterns=(_AARCH64_ZA_RE,),
    ),
    "arm": _VectorRegisters(
        ranges=(
            ("d", 0, 31),
            ("s", 0, 31),
            ("q", 0, 15),
            ("p", 0, 0),
        )
    ),
    "riscv": _VectorRegisters(ranges=(("v", 0, 31),)),
    "powerpc": _VectorRegisters(
        ranges=(
            ("vr", 0, 31),
            ("v", 0, 31),
            ("vs", 0, 63),
            ("cvr", 0, 31),
            ("cvs", 0, 63),
            ("ev", 0, 31),
        )
    ),
    "s390": _VectorRegisters(
        ranges=(("v", 0, 31),),
        patterns=(_S390_VECTOR_LOW_RE,),
    ),
    "loongarch": _VectorRegisters(ranges=(("vr", 0, 31), ("xr", 0, 31))),
    # GDB 15.1 does not expose MIPS MSA registers, so this entry is normally
    # inert.  Targets that do expose them name them w0-w31; ``w`` must not be
    # used outside MIPS because AArch64 uses it for the 32-bit GPR views.
    "mips": _VectorRegisters(ranges=(("w", 0, 31),)),
}


# Used when the family cannot be inferred.  Only names that never denote a
# non-vector register on the supported targets are listed; in particular ``v``,
# ``s``, ``d``, ``q``, ``p``, ``w``, and ``k`` stay out.  ``k0``/``k1`` are
# MIPS integer registers, so the x86 opmask mask registers are family-only.
_UNAMBIGUOUS_VECTOR_REGISTERS = _VectorRegisters(
    ranges=(
        ("xmm", 0, 31),
        ("ymm", 0, 31),
        ("zmm", 0, 31),
        ("mm", 0, 7),
        ("tmm", 0, 7),
        ("vr", 0, 31),
        ("xr", 0, 31),
        ("vs", 0, 63),
        ("cvr", 0, 31),
        ("cvs", 0, 63),
        ("ev", 0, 31),
        ("z", 0, 31),
    ),
    exact=frozenset({"ffr", "za", "zt0"}),
    patterns=(_AARCH64_ZA_RE,),
)


def guess_register_family(register_names: Iterable[str]) -> RegisterFamily | None:
    """Infer the register family from names that are unique to one target.

    Returns ``None`` when the available names are too generic to decide.  The
    caller then falls back to the unambiguous set, which can under-filter but
    never removes a register that is known to be non-vector somewhere.
    """

    available = {name.strip().casefold() for name in register_names if name.strip()}

    if {"pswm", "pswa"} & available:
        return "s390"
    if {"msr", "cr", "xer", "r0"} <= available:
        return "powerpc"
    if {"badv", "fcc0"} & available:
        return "loongarch"
    if {"lo", "hi"} <= available:
        return "mips"
    if {"zero", "gp", "tp"} <= available and "at" not in available:
        return "riscv"
    if {"vtype", "vlenb"} & available:
        return "riscv"
    if "cpsr" in available and ({"x0", "x30"} & available):
        return "aarch64"
    # GDB names the ARM program counter ``pc`` rather than ``r15``, so only
    # ``cpsr`` and ``r0`` are reliable.
    if {"cpsr", "r0"} <= available:
        return "arm"
    if ({"rax", "rip"} <= available) or ({"eax", "eip"} <= available):
        return "x86"
    if {"x31", "pc"} <= available and "cpsr" not in available:
        return "riscv"
    return None


def is_vector_register(family: RegisterFamily | None, name: str) -> bool:
    """Return whether NAME is a vector/SIMD data register for FAMILY."""

    spec = (
        _UNAMBIGUOUS_VECTOR_REGISTERS
        if family is None
        else _FAMILY_VECTOR_REGISTERS.get(family, _UNAMBIGUOUS_VECTOR_REGISTERS)
    )
    normalized = name.strip().casefold()
    if not normalized:
        return False
    if normalized in spec.exact:
        return True
    if any(pattern.match(normalized) for pattern in spec.patterns):
        return True
    for base, minimum, maximum in spec.ranges:
        if not normalized.startswith(base):
            continue
        suffix = normalized[len(base) :]
        if suffix.isdigit() and minimum <= int(suffix) <= maximum:
            return True
    return False
