"""Architecture-aware vector register classification tests."""

from __future__ import annotations

import pytest

from gdb_mcp.session.inspection.register_classes import (
    RegisterFamily,
    guess_register_family,
    is_vector_register,
)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["rax", "rip", "eflags", "xmm0"], "x86"),
        (["rax", "rbx", "rbp", "rsp", "rip", "eflags", "xmm0", "mxcsr"], "x86"),
        (["eax", "eip", "eflags"], "x86"),
        (["zero", "ra", "sp", "gp", "tp", "a0", "v0", "vtype"], "riscv"),
        (["zero", "ra", "sp", "gp", "tp", "t0", "fp", "s1", "a0", "pc"], "riscv"),
        (["x0", "x31", "pc", "f0", "fcsr"], "riscv"),
        (["x0", "x30", "sp", "pc", "cpsr", "v0", "z0", "p0", "ffr"], "aarch64"),
        (["x0", "x30", "sp", "pc", "cpsr", "v0", "q0", "d0", "w0", "fpcr"], "aarch64"),
        (["r0", "r12", "sp", "lr", "pc", "cpsr", "d0", "s0", "q0"], "arm"),
        (["zero", "at", "v0", "v1", "s0", "lo", "hi", "pc"], "mips"),
        (
            ["zero", "at", "v0", "v1", "a0", "s0", "s8", "k0", "gp", "sp", "ra", "lo", "hi"],
            "mips",
        ),
        (["r0", "r31", "pc", "msr", "cr", "xer", "vr0", "vs0"], "powerpc"),
        (["r0", "r31", "pc", "msr", "cr", "lr", "ctr", "xer", "fpscr", "vr0"], "powerpc"),
        (["pswm", "pswa", "r0", "r15", "v0"], "s390"),
        (["pswm", "pswa", "r0", "r15", "acr0", "fpc", "f0"], "s390"),
        (["r0", "r31", "pc", "badv", "f0", "fcc0", "vr0"], "loongarch"),
        (["r0", "r31", "orig_a0", "pc", "badv", "fcc0", "fcsr", "vr0", "xr0"], "loongarch"),
        (["pc", "sp"], None),
    ],
)
def test_guess_register_family(names: list[str], expected: RegisterFamily | None) -> None:
    assert guess_register_family(names) == expected


@pytest.mark.parametrize(
    ("family", "name", "expected"),
    [
        ("x86", "xmm0", True),
        ("x86", "xmm31", True),
        ("x86", "xmm32", False),
        ("x86", "ymm15", True),
        ("x86", "zmm31", True),
        ("x86", "mm7", True),
        ("x86", "k7", True),
        ("x86", "tmm7", True),
        ("x86", "st0", False),
        ("x86", "mxcsr", False),
        ("x86", "bnd0", False),
        ("aarch64", "v31", True),
        ("aarch64", "q31", True),
        ("aarch64", "d31", True),
        ("aarch64", "s31", True),
        ("aarch64", "h31", True),
        ("aarch64", "b31", True),
        ("aarch64", "z31", True),
        ("aarch64", "p15", True),
        ("aarch64", "p16", False),
        ("aarch64", "z32", False),
        ("aarch64", "ffr", True),
        ("aarch64", "za", True),
        ("aarch64", "zt0", True),
        ("aarch64", "za0b", True),
        ("aarch64", "za0hb0", True),
        ("aarch64", "za15vq255", True),
        ("aarch64", "w0", False),
        ("aarch64", "x0", False),
        ("aarch64", "fpsr", False),
        ("arm", "d0", True),
        ("arm", "s31", True),
        ("arm", "q15", True),
        ("arm", "p0", True),
        ("arm", "P0", True),
        ("arm", "f0", False),
        ("arm", "r0", False),
        ("riscv", "v0", True),
        ("riscv", "v31", True),
        ("riscv", "s0", False),
        ("riscv", "s11", False),
        ("riscv", "fp", False),
        ("riscv", "f0", False),
        ("riscv", "vl", False),
        ("riscv", "vtype", False),
        ("powerpc", "vr0", True),
        ("powerpc", "v31", True),
        ("powerpc", "vs63", True),
        ("powerpc", "cvr0", True),
        ("powerpc", "cvs63", True),
        ("powerpc", "ev31", True),
        ("powerpc", "vscr", False),
        ("powerpc", "vrsave", False),
        ("powerpc", "f0", False),
        ("s390", "v0", True),
        ("s390", "v31", True),
        ("s390", "v0l", True),
        ("s390", "v15l", True),
        ("s390", "v16l", False),
        ("s390", "f0", False),
        ("loongarch", "vr0", True),
        ("loongarch", "xr31", True),
        ("loongarch", "f0", False),
        ("mips", "v0", False),
        ("mips", "v1", False),
        ("mips", "s0", False),
        ("mips", "lo", False),
        ("mips", "w0", True),
        ("mips", "w31", True),
        (None, "xmm0", True),
        (None, "vr0", True),
        (None, "z0", True),
        (None, "ffr", True),
        (None, "v0", False),
        (None, "s0", False),
        (None, "d0", False),
        (None, "q0", False),
        (None, "p0", False),
        (None, "w0", False),
        (None, "k0", False),
    ],
)
def test_is_vector_register(family: RegisterFamily | None, name: str, expected: bool) -> None:
    assert is_vector_register(family, name) is expected


def test_is_vector_register_rejects_empty_name() -> None:
    assert is_vector_register("x86", "") is False
    assert is_vector_register(None, "  ") is False
