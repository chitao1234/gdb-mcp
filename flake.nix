{
  description = "Curated compatibility toolchains for gdb-mcp";

  inputs = {
    nixpkgs-2311.url = "github:NixOS/nixpkgs/nixos-23.11";
    nixpkgs-2405.url = "github:NixOS/nixpkgs/nixos-24.05";
    nixpkgs-2411.url = "github:NixOS/nixpkgs/nixos-24.11";
    nixpkgs-2505.url = "github:NixOS/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    # Oldest baseline and intermediate debugger pins. Python comes from the
    # modern inputs above; only GDB 7.1a's embedded Python 2.6 is old.
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
    nixpkgs-2009 = {
      url = "github:NixOS/nixpkgs/20.09";
      flake = false;
    };
    nixpkgs-2105 = {
      url = "github:NixOS/nixpkgs/21.05";
      flake = false;
    };
    nixpkgs-2111 = {
      url = "github:NixOS/nixpkgs/21.11";
      flake = false;
    };
    nixpkgs-2305 = {
      url = "github:NixOS/nixpkgs/23.05";
      flake = false;
    };
  };

  outputs =
    inputs@{ nixpkgs-2311, ... }:
    let
      inherit (nixpkgs-2311) lib;
      systems = [ "x86_64-linux" ];

      packagesFor =
        system: input:
        if builtins.isAttrs input && input ? legacyPackages then
          input.legacyPackages.${system}
        else
          import input { inherit system; };

      profiles = {
        oldest-py310-gdb71a-lldb40-delve120 = {
          baseline = true;
        };
        legacy-py311-gdb92-lldb71-delve120 = {
          legacy = true;
          pythonPackages = inputs.nixpkgs-2405;
          python = "python311";
          gdbPackages = inputs.nixpkgs-2009;
          lldbPackages = inputs.nixpkgs-2009;
          goPackages = inputs.nixpkgs-2305;
          delvePackages = inputs.nixpkgs-2305;
        };
        legacy-py312-gdb111-lldb111-delve121 = {
          legacy = true;
          pythonPackages = inputs.nixpkgs-2411;
          python = "python312";
          gdbPackages = inputs.nixpkgs-2111;
          lldbPackages = inputs.nixpkgs-2105;
          goPackages = inputs.nixpkgs-2311;
          delvePackages = inputs.nixpkgs-2311;
        };
        full-py313-gdb13-lldb14-delve122 = {
          pythonPackages = inputs.nixpkgs-2505;
          python = "python313";
          gdbPackages = inputs.nixpkgs-2311;
          lldbPackages = inputs.nixpkgs-2311;
          goPackages = inputs.nixpkgs-2405;
          delvePackages = inputs.nixpkgs-2405;
        };
        full-py314-gdb15-lldb18-delve123 = {
          pythonPackages = inputs.nixpkgs-unstable;
          python = "python314";
          gdbPackages = inputs.nixpkgs-2411;
          lldbPackages = inputs.nixpkgs-2411;
          goPackages = inputs.nixpkgs-2411;
          delvePackages = inputs.nixpkgs-2411;
        };
        full-py313-gdb17-lldb21-delve127 = {
          pythonPackages = inputs.nixpkgs-2505;
          python = "python313";
          gdbPackages = inputs.nixpkgs-unstable;
          lldbPackages = inputs.nixpkgs-unstable;
          goPackages = inputs.nixpkgs-unstable;
          delvePackages = inputs.nixpkgs-unstable;
        };
      };

      mkMixedShell =
        system: name: spec:
        let
          pkgs = nixpkgs-2311.legacyPackages.${system};
          pythonPkgs = packagesFor system spec.pythonPackages;
          gdbPkgs = packagesFor system spec.gdbPackages;
          lldbPkgs = packagesFor system spec.lldbPackages;
          goPkgs = packagesFor system spec.goPackages;
          delvePkgs = packagesFor system spec.delvePackages;
          lldb = lldbPkgs.lldb;
          lldbLib = if lldb ? lib then lldb.lib else lldb;
          lldbFull =
            if lldb ? dev then
              pkgs.symlinkJoin {
                name = "lldb-${lldb.version or lldb.name}-with-headers";
                paths = [
                  lldb
                  lldb.dev
                  lldbLib
                ];
              }
            else
              lldb;
        in
        pkgs.mkShell {
          name = "gdb-mcp-${name}";
          packages = [
            pkgs.gcc
            pythonPkgs.${spec.python}
            gdbPkgs.gdb
            lldbFull
            goPkgs.go
            delvePkgs.delve
            pkgs.binutils
          ];
          GOTOOLCHAIN = "local";
          LLDB_PREFIX = "${lldbFull}";
          LLDB_BRIDGE_CXX = "${lldbPkgs.gcc}/bin/g++";
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
          p2305 = import inputs.nixpkgs-2305 { inherit system; };

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
          go120 = p2305.go;
          delve120 = p2305.delve;
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
            legacy.go120
            legacy.delve120
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
          if spec ? baseline then mkBaselineShell system name else mkMixedShell system name spec
        ) profiles
      );
      ciProfileNames = builtins.attrNames profiles;
    };
}
