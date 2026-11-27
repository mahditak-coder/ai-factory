# AUDIT-TEST-REPORT
Command: `AI_FACTORY_TEST_ROOT=<isolated> python3 -m unittest discover -s core/tests -p 'test_*.py'`
Result: exit 1; Ran 222 tests.
Failures/errors (NOT hidden, NOT fixed):
FAIL: test_root_isolated_when_env_set (test_core_units.IsolationTest.test_root_isolated_when_env_set)
Service/model/paid paths requiring live credentials were NOT TESTED. Raw log: audit-evidence/test-run.log.
