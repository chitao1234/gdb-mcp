{
  description = "Curated compatibility toolchains for gdb-mcp";

  inputs = {
    nixpkgs-2311.url = "github:NixOS/nixpkgs/nixos-23.11";
    nixpkgs-2405.url = "github:NixOS/nixpkgs/nixos-24.05";
    nixpkgs-2411.url = "github:NixOS/nixpkgs/nixos-24.11";
    nixpkgs-2505.url = "github:NixOS/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    # Oldest baseline: GDB 7.1a with its Python 2.6, LLDB 4.0.1, and the
    # matching compilers/Delve are pinned separately from the server Python.
    nixpkgs-1310 = {
      url = "github:NixOS/nixpkgs/release-13.10";
      flake = false;
    };
    nixpkgs-1609 = {
      url = "github:NixOS/nixpkgs/16.09";
      flake = false;
    };
    nixpkgs-1709 = {
      url = "github:NixOS/nixpkgs/17.09";
      flake = false;
    };
    nixpkgs-2105 = {
      url = "github:NixOS/nixpkgs/21.05";
      flake = false;
    };
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
        oldest-py310-gdb71a-lldb40-delve16 = {
          channel = "mixed";
          baseline = true;
        };
      };

      mkShell =
        system: name: spec:
        let
          pkgs = spec.packages.${system};
          lldb = pkgs.${spec.lldb};
          lldbLib = if lldb ? lib then lldb.lib else lldb;
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

      legacyFor =
        system:
        let
          p1310 = import inputs.nixpkgs-1310 { inherit system; };
          p1609 = import inputs.nixpkgs-1609 {
            inherit system;
            config.allowBroken = true;
          };
          p1709 = import inputs.nixpkgs-1709 { inherit system; };
          p2105 = import inputs.nixpkgs-2105 { inherit system; };

          python26 = p1609.lib.overrideDerivation p1609.python26 (old: {
            buildInputs = old.buildInputs ++ [ p1609.glibc.dev ];
            preConfigure = ''
              for i in /usr /sw /opt /pkg; do
                substituteInPlace ./setup.py --replace $i /no-such-path
              done
              for i in Lib/plat-*/regen; do
                substituteInPlace $i --replace /usr/include/ ${p1609.glibc.dev}/include/
              done
            '';
          });

          python26Path = p1609.runCommand "python26-path" { } ''
            mkdir -p $out/bin
            ln -s ${python26}/bin/python2.6 $out/bin/python2.6
          '';

          gdb71a = p1609.stdenv.mkDerivation {
            name = "gdb-7.1a";
            src = p1609.fetchurl {
              url = "https://ftp.gnu.org/gnu/gdb/gdb-7.1a.tar.bz2";
              sha256 = "1h9nrdy3a78zmib9i9x0m3by3pm6c2vqhmygf7yywd9icfc06dx4";
            };
            nativeBuildInputs = [
              p1609.pkgconfig
              p1609.texinfo
              p1609.perl
            ];
            buildInputs = [
              p1609.ncurses
              p1609.readline
              p1609.gmp
              p1609.mpfr
              p1609.expat
              p1609.zlib
              python26
            ];
            configureFlags = [
              "--disable-werror"
              "--with-python=${python26}"
              "--with-system-readline"
              "--with-system-zlib"
              "--with-expat"
              "--with-libexpat-prefix=${p1609.expat.dev}"
              "--with-gmp=${p1609.gmp.dev}"
              "--with-mpfr=${p1609.mpfr.dev}"
            ];
            enableParallelBuilding = true;
            NIX_CFLAGS_COMPILE = "-D_GNU_SOURCE";
            postPatch = ''
              find gdb \( -name '*.c' -o -name '*.h' \) -print0 \
                | xargs -0 sed -i 's/struct siginfo/siginfo_t/g'
              substituteInPlace gdb/configure.tgt \
                --replace 'build_gdbserver=yes' 'build_gdbserver=no'
            '';
            doCheck = false;
            postInstall = ''
              rm -v $out/share/info/bfd.info
            '';
          };
        in
        {
          inherit
            python26
            python26Path
            gdb71a
            ;
          lldb40 = p1709.lldb;
          gcc46 = p1310.gcc;
          gcc64 = p1709.gcc;
          go116 = p2105.go;
          delve16 = p2105.delve;
        };

      mkBaselineShell =
        system: name:
        let
          pkgs = nixpkgs-2311.legacyPackages.${system};
          legacy = legacyFor system;
        in
        pkgs.mkShell {
          name = "gdb-mcp-${name}";
          packages = [
            legacy.gcc46
            legacy.gdb71a
            legacy.lldb40
            legacy.go116
            legacy.delve16
            legacy.python26Path
            pkgs.python310
            pkgs.binutils
          ];
          GOTOOLCHAIN = "local";
          LLDB_PREFIX = "${legacy.lldb40}";
          LLDB_BRIDGE_CXX = "${legacy.gcc64}/bin/g++";
          PYTHON26_PATH = "${legacy.python26}";
          PYTHONPATH = "";
          shellHook = ''
            unset PYTHONPATH
          '';
        };
    in
    {
      devShells = lib.genAttrs systems (
        system:
        builtins.mapAttrs (
          name: spec:
          if spec ? baseline then mkBaselineShell system name else mkShell system name spec
        ) profiles
      );
      ciProfileNames = builtins.attrNames profiles;
    };
}
