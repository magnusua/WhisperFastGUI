"""When several Pythons exist, ask unless the user already confirmed the same set."""
import os
import unittest

from whisperfast.setup.python_selector import (
    _discovered_ids,
    _should_prompt_python_choice,
)


def _cand(path):
    return {"path": path}


class TestShouldPromptPythonChoice(unittest.TestCase):
    def test_single_python_never_prompts(self):
        c = [_cand(os.path.join("py", "3.12", "python.exe"))]
        self.assertFalse(_should_prompt_python_choice(c, c[0]["path"], {}))

    def test_two_pythons_prompt_if_never_chosen(self):
        c = [
            _cand(os.path.join("py", "3.12", "python.exe")),
            _cand(os.path.join("py", "3.13", "python.exe")),
        ]
        self.assertTrue(_should_prompt_python_choice(c, c[1]["path"], {
            "python_path_chosen": False,
        }))

    def test_two_pythons_no_prompt_after_confirm_same_set(self):
        c = [
            _cand(os.path.join("py", "3.12", "python.exe")),
            _cand(os.path.join("py", "3.13", "python.exe")),
        ]
        ids = _discovered_ids(c)
        self.assertFalse(_should_prompt_python_choice(c, c[0]["path"], {
            "python_path_chosen": True,
            "python_discovered": ids,
        }))

    def test_prompts_again_when_new_install_appears(self):
        old = [_cand(os.path.join("py", "3.13", "python.exe"))]
        now = [
            _cand(os.path.join("py", "3.12", "python.exe")),
            _cand(os.path.join("py", "3.13", "python.exe")),
        ]
        self.assertTrue(_should_prompt_python_choice(now, old[0]["path"], {
            "python_path_chosen": True,
            "python_discovered": _discovered_ids(old),
        }))


if __name__ == "__main__":
    unittest.main()
