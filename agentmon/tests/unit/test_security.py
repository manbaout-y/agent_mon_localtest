import unittest

from agentmon.security import command_hash


class SecurityTests(unittest.TestCase):
    def test_command_hash_is_stable_and_salt_changes_digest(self) -> None:
        argv = ["/bin/bash", "-lc", "echo secret"]

        self.assertEqual(command_hash(argv), command_hash(argv))
        self.assertNotEqual(command_hash(argv), command_hash(argv, salt="local-salt"))
