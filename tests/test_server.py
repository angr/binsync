import git
import os
import pathlib
import sys
import tempfile
import toml

import unittest

import binsync.data
from binsync.core.client import Client


class TestServer(unittest.TestCase):
    def test_server_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Client("user0", tmpdir, "fake_hash", init_repo=True, use_git_server=True)
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, ".git")))
            client.git_server.stop_server()


    def test_server_echo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Client("user0", tmpdir, "fake_hash", init_repo=True, use_git_server=True)
            self.assertEqual(client.echo("hello world!"), "hello world!")
            # Terminate the server
            client.git_server.stop_server()

    def test_client_server_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Client("user0", tmpdir, "fake_hash", init_repo=True, use_git_server=True)

            state = client.get_state()
            self.assertEqual(state.user, "user0")
            # after create, state is dirty
            self.assertTrue(state.dirty)

            func_header = binsync.data.FunctionHeader("some_name", 0x400080)
            state.set_function_header(func_header)
            # it should be dirty still (more edits)
            self.assertTrue(state.dirty)

            # commit changes so we clean it!
            client.commit_state(state)
            self.assertFalse(state.dirty)

            state = client.get_state(user="user0")
            self.assertTrue(len(state.functions), 1)
            self.assertTrue(state.functions[0x400080].header, func_header)

            # git is still running at least on windows
            client.close()
            client.git_server.stop_server()

    def test_invalid_client_server_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Client("user0", tmpdir, "fake_hash", init_repo=True)
            state = client.get_state()
            func_header = binsync.data.FunctionHeader("some_name", 0x400080)
            state.set_function_header(func_header)
            client.commit_state(state)
            client.close()
            del client

            # The lock file may not be released yet, so do it ourselves
            (pathlib.Path(tmpdir) / ".git/binsync.lock").unlink()

            with open(pathlib.Path(tmpdir) / "functions" / "00400080.toml", "r+") as file:
                file.truncate(5)

            repo = git.Repo(tmpdir)
            repo.git.add(all=True)
            repo.index.commit("corrupt")

            client = Client("user0", tmpdir, "fake_hash")
            self.assertRaises(toml.decoder.TomlDecodeError, lambda: client.get_state())


if __name__ == "__main__":
    unittest.main(argv=sys.argv)
