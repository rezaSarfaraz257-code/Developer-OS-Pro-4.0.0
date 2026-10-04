import unittest
from build_engine import detect_build, plan

class BuildEngineTests(unittest.TestCase):
    def test_vite_build(self):
        result=detect_build({"package.json":'{"scripts":{"build":"vite build"}}',"vite.config.js":""})
        self.assertEqual(result["kind"],"vite")
        self.assertEqual(result["command"],"npm run build")
    def test_django_check(self):
        self.assertEqual(detect_build({"manage.py":""})["kind"],"django")
    def test_go_build(self):
        self.assertEqual(detect_build({"go.mod":""})["command"],"go build ./...")
    def test_unknown(self):
        self.assertIsNone(detect_build({"README.md":"hello"}))
    def test_plan_steps(self):
        self.assertEqual(plan({"Cargo.toml":""})["steps"][-1],"verify")

if __name__=="__main__": unittest.main()
