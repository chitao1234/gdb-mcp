{
  description = "Curated compatibility toolchains for gdb-mcp";

  inputs = {
    nixpkgs-2311.url = "github:NixOS/nixpkgs/nixos-23.11";
    nixpkgs-2405.url = "github:NixOS/nixpkgs/nixos-24.05";
    nixpkgs-2411.url = "github:NixOS/nixpkgs/nixos-24.11";
    nixpkgs-2505.url = "github:NixOS/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs =
    inputs@{ nixpkgs-2311, ... }:
    let
      inherit (nixpkgs-2311) lib;
      systems = [ "x86_64-linux" ];

      # One row per era, not a cartesian product. Each profile takes Python,
      # GDB, LLDB, Go, and Delve from the same nixpkgs snapshot so the
      # versions were actually released and built together.
      profiles = {
        py310-gdb13-lldb14-delve121 = {
          channel = "nixos-23.11";
          packages = inputs.nixpkgs-2311.legacyPackages;
          python = "python310";
          lldb = "lldb";
        };
        py311-gdb14-lldb17-delve122 = {
          channel = "nixos-24.05";
          packages = inputs.nixpkgs-2405.legacyPackages;
          python = "python311";
          lldb = "lldb";
        };
        py312-gdb15-lldb18-delve123 = {
          channel = "nixos-24.11";
          packages = inputs.nixpkgs-2411.legacyPackages;
          python = "python312";
          lldb = "lldb";
        };
        py313-gdb16-lldb19-delve124 = {
          channel = "nixos-25.05";
          packages = inputs.nixpkgs-2505.legacyPackages;
          python = "python313";
          lldb = "lldb";
        };
        py314-gdb17-lldb21-delve127 = {
          channel = "nixpkgs-unstable";
          packages = inputs.nixpkgs-unstable.legacyPackages;
          python = "python314";
          lldb = "lldb";
        };
      };

      mkShell =
        system: name: spec:
        let
          pkgs = spec.packages.${system};
          lldb = pkgs.${spec.lldb};
          # Older nixpkgs split liblldb out of the default output.
          lldbLib = if lldb ? lib then lldb.lib else lldb;
          # The bridge searches one prefix for bin, lib, and headers.
          lldbFull = pkgs.symlinkJoin {
            name = "lldb-${lldb.version}-with-headers";
            paths = [
              lldb
              lldb.dev
              lldbLib
            ];
          };
        in
        pkgs.mkShell {
          name = "gdb-mcp-${name}";
          packages = [
            pkgs.${spec.python}
            pkgs.gdb
            lldbFull
            pkgs.go
            pkgs.delve
            pkgs.gcc
            pkgs.binutils
          ];
          GOTOOLCHAIN = "local";
          LLDB_PREFIX = "${lldbFull}";
          # Pin the bridge compiler so a host-exported CXX cannot leak into
          # this profile's bridge build.
          LLDB_BRIDGE_CXX = "${pkgs.gcc}/bin/c++";
        };
    in
    {
      devShells = lib.genAttrs systems (system: builtins.mapAttrs (mkShell system) profiles);
      ciProfileNames = builtins.attrNames profiles;
    };
}
