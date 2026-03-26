"""Session configuration models."""

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class SessionConfig:
    """Normalized launch configuration for a GDB session."""

    program: str | None = None
    args: tuple[str, ...] = ()
    init_commands: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    gdb_path: str | None = None
    working_dir: str | None = None
    core: str | None = None

    @classmethod
    def from_inputs(
        cls,
        *,
        program: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        gdb_path: str | None = None,
        working_dir: str | None = None,
        core: str | None = None,
    ) -> "SessionConfig":
        """Build an immutable session config from mutable API inputs."""

        return cls(
            program=program,
            args=tuple(args or ()),
            init_commands=tuple(init_commands or ()),
            env=dict(env or {}),
            gdb_path=gdb_path,
            working_dir=working_dir,
            core=core,
        )
