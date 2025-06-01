from __future__ import print_function

import sys

import pytest

from .util import silence

try:
    from configparser import ConfigParser
except ImportError:
    from ConfigParser import ConfigParser

import os
import re

from .constants import EXIT_INTERRUPTED, EXIT_NOTESTSCOLLECTED, EXIT_OK


class MultiLineConfigParser(ConfigParser):
    def get(self, section, option, raw=False, vars=None, fallback=None):
        value = super().get(section, option, raw, vars, fallback)
        if "\n" in value:
            return value.split("\n")
        return value

    def read(self, filenames, encoding=None):
        if isinstance(filenames, (str, bytes, os.PathLike)):
            filenames = [filenames]

        for filename in filenames:
            try:
                with open(filename, encoding=encoding) as f:
                    content = f.read()
            except Exception:
                # Fall back to default behavior if file can't be opened
                super().read(filenames, encoding=encoding)
                return

            # Transform TOML/INI-style array values into newline-separated values
            # Pattern matches: key = [ item1, item2, ... ]
            array_pattern = re.compile(
                r"^(?P<key>\s*[^=\s]+\s*=\s*)\[(?P<items>.*?)\]",
                re.DOTALL | re.MULTILINE,
            )

            def _replace_array(match):
                items = [
                    item.strip().strip('"').strip("'")
                    for item in match.group("items").split(",")
                    if item.strip()
                ]
                return f"{match.group('key')}{'\\n'.join(items)}"

            processed = array_pattern.sub(_replace_array, content)

            # Read the processed content as if it were the file
            self.read_string(processed, source=filename)


CLI_OPTION_PREFIX = "--"


class CollectError(Exception):
    pass


class StopCollect(Exception):
    pass


class CollectConfig(object):
    """
    A pytest plugin to gets the configuration file.
    """

    def __init__(self):
        self.path = None

    def pytest_cmdline_main(self, config):
        if hasattr(config, "inifile"):
            # pytest >= 2.7.0
            inifile = config.inifile
        else:
            # pytest < 2.7.0
            inifile = config.inicfg.config.path
        if inifile:
            self.path = str(inifile)
            raise StopCollect()


def _run_pytest_collect(pytest_args):
    collect_config_plugin = CollectConfig()
    argv = pytest_args + ["--collect-only"]

    try:
        exit_code = pytest.main(argv, plugins=[collect_config_plugin])
    except StopCollect:
        return collect_config_plugin.path

    if exit_code == EXIT_INTERRUPTED:
        # pytest raises EXIT_INTERRUPTED on *both* keyboard error and parse errors
        raise KeyboardInterrupt()
    if exit_code not in [EXIT_OK, EXIT_NOTESTSCOLLECTED]:
        raise CollectError()

    return collect_config_plugin.path


def _collect_config(pytest_args, silent=True):
    if silent:
        try:
            with silence():
                return _run_pytest_collect(pytest_args)
        except (KeyboardInterrupt, Exception, SystemExit):
            pass

        # Print message and run again without silencing
        print(
            "Error: Could not run --collect-only to handle the pytest "
            "config file. Trying again without silencing output...",
            file=sys.stderr,
        )

    # Collect without silencing
    return _run_pytest_collect(pytest_args)


def merge_config(args, pytest_args, silent=True, verbose=False):
    if verbose:
        print("Locating inifile...")

    try:
        config_path = _collect_config(pytest_args, silent)
    except (KeyboardInterrupt, CollectError):
        return False

    if not config_path:
        return True

    config = MultiLineConfigParser()
    config.read(config_path)
    if not config.has_section("pytest-watch"):
        return True

    for cli_name in args:
        if not cli_name.startswith(CLI_OPTION_PREFIX):
            continue
        config_name = cli_name[len(CLI_OPTION_PREFIX) :]

        # Let CLI options take precedence
        if args[cli_name]:
            continue

        # Find config option
        if not config.has_option("pytest-watch", config_name):
            continue

        # Merge config option using the expected type
        if isinstance(args[cli_name], list):
            value = config.get("pytest-watch", config_name)
            if isinstance(value, list):
                args[cli_name].extend(value)
            else:
                args[cli_name].append(value)
        elif isinstance(args[cli_name], bool):
            args[cli_name] = config.getboolean("pytest-watch", config_name)
        else:
            args[cli_name] = config.get("pytest-watch", config_name)

    return True
