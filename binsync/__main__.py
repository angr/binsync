import argparse
import logging
from pathlib import Path
import os

from libbs.decompilers import SUPPORTED_DECOMPILERS, GHIDRA_DECOMPILER, ANGR_DECOMPILER, IDA_DECOMPILER, BINJA_DECOMPILER

from binsync.installer import BinSyncInstaller
from binsync.extras import EXTRAS_AVAILABLE
import binsync
import libbs

l = logging.getLogger(__name__)


def install():
    BinSyncInstaller().install()


def install_angr(path):
    if not path.exists():
        print("Path does not exist, please rerun with a valid path.")
        return

    installer = BinSyncInstaller()
    installer.install_angr(path=path)


def main():
    parser = argparse.ArgumentParser(
        description="""
        The BinSync Command Line Util. This is the script interface to BinSync that allows you to
        do a variety of things that are independent of running in a decompiler like installing, 
        testing plugin code, and merging databases.
        """,
        epilog="""
        Examples:
        binsync --install
        """
    )
    parser.add_argument(
        "-i", "--install", action="store_true", help="""
        Install the BinSync core to supported interface_overrides as plugins. This option will start an interactive
        prompt asking for install paths for all supported interface_overrides. Each install path is optional and 
        will be skipped if not path is provided during install. 
        """
    )
    parser.add_argument(
        "--cli-install", type=str, choices=SUPPORTED_DECOMPILERS, help="""
        Does a non-interactive install useful for installing in a script or in a docker container. 
        Must be used with the `--install-path` parameter.
        """
    )
    parser.add_argument(
        "--install-path", type=Path, help="""
        Does a non-interactive install for angr only from the provided path.
        """
    )
    parser.add_argument(
        "-s", "--server", choices=[GHIDRA_DECOMPILER], help="""
        Execute the decompiler server for headless connection (only Ghidra supported).
        """
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"BinSync {binsync.__version__} | LibBS {libbs.__version__}"
    )
    if EXTRAS_AVAILABLE:
        parser.add_argument(
            "-ai", help="""
            Add an AI user to a BinSync project. This feature is an extra that requires install of the [extra] pip
            version of BiNSync. An AI user also requires an OpenAI API key, which is a required option if you use this.
            The AI user will add a series of changes to the project using various prompts. See DAILA project for
            more info. 
            """,
            action="store_true"
        )
        parser.add_argument(
            "--openai-api-key", help="""
            An optional specification of your OpenAPI key. If not used, then the key must exist in your env as 
            OPENAI_API_KEY for use with the AI feature. 
            """,
            type=str
        )
        parser.add_argument(
            "--proj-path", help="""
            The path to the BinSync project path associated with the Extras command.
            """,
            type=Path
        )
        parser.add_argument(
            "--binary-path", help="""
            The path to the binary that should be associated with the earlier used --proj-path option.
            """,
            type=Path
        )
        parser.add_argument(
            "--username", help="""
            The optional Username of the user that will be added or modified in the extras command. 
            """,
            type=str
        )
        parser.add_argument(
            "--decompiler", help="""
            The optional decompiler of that will be used in the extras command. 
            """,
            type=str,
            choices=SUPPORTED_DECOMPILERS
        )

    args = parser.parse_args()

    if args.cli_install:
        install_path = args.install_path
        if install_path is None:
            raise RuntimeError("You must provide an install path via --install-path if you use CLI install")

        install_path = Path(install_path).expanduser().absolute()
        if not install_path.exists():
            raise ValueError("You must provide a valid path for CLI install")

        installer = BinSyncInstaller()
        target = args.cli_install
        if target == ANGR_DECOMPILER:
            install_func = installer.install_angr
        elif target == IDA_DECOMPILER:
            install_func = installer.install_ida
        elif target == BINJA_DECOMPILER:
            install_func = installer.install_binja
        elif target == GHIDRA_DECOMPILER:
            install_func = installer.install_ghidra
        else:
            raise ValueError("Invalid install target choice")

        if target == ANGR_DECOMPILER:
            install_func(path=install_path, interactive=False, force=True)
        else:
            install_func(path=install_path, interactive=False)
    elif args.install:
        install()

    if args.server:
        if args.server != GHIDRA_DECOMPILER:
            raise ValueError("Only Ghidra is supported for use as a server")

        from binsync.interface_overrides.ghidra import start_ghidra_ui
        start_ghidra_ui()

    if EXTRAS_AVAILABLE and args.ai:
        if not (args.proj_path and args.binary_path):
            l.error("Using the AI feature requires you to specify the binary path and project path with cli options.")
            return
        
        ai_key = args.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not ai_key:
            l.error("Using the AI feature requires an OpenAI API Key. Either specify it or set it in the "
                    "OPENAI_API_KEY environment variable.")
            return

        from binsync.extras import add_ai_user_to_project
        l.info("Starting AI queries...")
        extra_args = {}
        if args.username:
            extra_args['username'] = args.username

        add_ai_user_to_project(
            ai_key, args.binary_path, args.proj_path, decompiler_backend=args.decompiler, **extra_args
        )


if __name__ == "__main__":
    main()
