"""Session configuration models."""

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class SessionConfig:
    """Normalized launch configuration for a GDB session."""

    program: str | None = None
    debugger: str = "gdb"
    args: tuple[str, ...] = ()
    init_commands: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    gdb_path: str | None = None
    gdb_args: tuple[str, ...] = ()
    working_dir: str | None = None
    core: str | None = None
    endpoint: str | None = None
    rr_replay: str | None = None

    @classmethod
    def from_inputs(
        cls,
        *,
        program: str | None = None,
        debugger: str | None = None,
        args: list[str] | None = None,
        init_commands: list[str] | None = None,
        env: dict[str, str] | None = None,
        gdb_path: str | None = None,
        gdb_args: list[str] | None = None,
        working_dir: str | None = None,
        core: str | None = None,
        endpoint: str | None = None,
        rr_replay: str | None = None,
    ) -> "SessionConfig":
        """Build an immutable session config from mutable API inputs."""

        return cls(
            program=program,
            debugger=debugger or "gdb",
            args=tuple(args or ()),
            init_commands=tuple(init_commands or ()),
            env=dict(env or {}),
            gdb_path=gdb_path,
            gdb_args=tuple(gdb_args or ()),
            working_dir=working_dir,
            core=core,
            endpoint=endpoint,
            rr_replay=rr_replay,
        )
