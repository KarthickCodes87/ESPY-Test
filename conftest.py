from __future__ import annotations

import os
import pytest
import requests
import stat
import subprocess
import tempfile

DEBUG = False
ESTEST_FILE = './ESTest.pl'
ESTEST_TIMEOUT = 1000
GREEN_MODE = True

def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".list" and file_path.name.startswith("crypto_tests"):
        return ESTestSuiteFile.from_parent(parent, path=file_path)

# Generate a new test file based on dynamic conditions.
class ESTestHook:
    def __init__(self, test_list):
        self.temp_file = None
        self.return_code = 0
        self.file_names = test_list
    
    def call_estest(self):
        def find_test_file_location(file_names):
            final_list = ''
            for test_file_name in file_names:
                final_list += 'tests/root/cryptoserver/{test_file_name}'
                final_list += ' '
            return final_list
        
        def run_estest_cmd(*args):
            tmpfile, tmpname = tempfile.mkstemp('.pl', 'tmp_', '.', text=True)
            self.temp_file = tmpname
            tests_to_run = "%s" % (find_test_file_location(self.file_names))
            os.write(tmpfile, (r'''#!/usr/bin/perl
{
    local @ARGV = ("-root", "-log_sections=all", "-logdir=logs/", "%s");
    do '%s';
}
''' % (tests_to_run, ESTEST_FILE)).encode('utf-8'))

            os.close(tmpfile)
            os.chmod(tmpname, stat.S_IXUSR)
            retcode = subprocess.call(
                "%s" % (self.temp_file), 
                shell=True, 
                timeout=ESTEST_TIMEOUT,
                stderr=subprocess.PIPE, 
                stdout=subprocess.PIPE)
            self.return_code = retcode
            if not DEBUG:
                os.remove(self.temp_file)
        run_estest_cmd()


class ESTestSuiteFile(pytest.File): 
    def collect(self):
        # read all the tests in the test list 
        # before calling the the wrapper for ESTest, we can use this to rearrange the test list order
        #   based on 
        #     a) how stable it is
        #     b) skip unstable tests (if required)
        #     c) prioritize based on some other criterias (test owners could decide)
        with open('crypto_tests.list') as fd:
            flakey_tests = []
            test_list_file_handle, test_list_file_name = tempfile.mkstemp('.list', 'crypto_tests_', '.', text=True)
            print(f"Starting to write stable tests first in {test_list_file_name}")
            for test_name in fd:
                #  GREEN MODE - construct test list with stable ones at the top
                if GREEN_MODE:
                    test_status = requests.get("http://localhost:8000/")
                    if test_status == 'FALSE':
                        flakey_tests.append(test_name)
                        continue
                    os.write(test_list_file_handle, test_name)

            print(f"Starting to write flakey tests in {test_list_file_name}")
            os.write(test_list_file_handle, r'''# cryptoserver tests that are flakey''')
            for flakey_t in flakey_tests: 
                os.write(test_list_file_handle, flakey_t)
            yield ESTestItem.from_parent(self, name=test_list_file_name, spec="crypto")


class ESTestItem(pytest.Item):
    def __init__(self, *, spec, **kwargs):
        super().__init__(**kwargs)
        self.spec = spec

    def runtest(self):
        obj_estest = ESTestHook(self.name)
        obj_estest.call_estest()
        if obj_estest.return_code:
            raise ESTestFailureException(self)

    def repr_failure(self, excinfo):
        """Called when self.runtest() raises an exception.""" 
        if isinstance(excinfo.value, ESTestFailureException):
            return "\n".join(["execution failed"])
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"testcase: {self.name}"


class ESTestFailureException(Exception):
    """Custom exception for error reporting."""
