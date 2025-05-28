from __future__ import annotations

import os
import pytest
import requests
import stat
import subprocess
import tempfile
import logging

DEBUG = False
ESTEST_FILE = './ESTest.pl'
EXTERNAL_SERVER = "http://localhost:8000/"
GREEN_MODE = os.getenv('GREEN_MODE', 'True').lower() == 'true'  # Read from ENV, default to True
#SUITE_TIMEOUT = 1000
TEST_LIST_FILE = 'crypto_tests.list'

logging.basicConfig(level=logging.INFO if DEBUG else logging.WARNING)
logger = logging.getLogger(__name__)

def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".list" and file_path.name.startswith(TEST_LIST_FILE):
        logger.info(f"Found ESTest suite file {TEST_LIST_FILE} in {file_path}")
        return ESTestSuiteFile.from_parent(parent=parent, fspath=file_path)


# Split the test list into two parts - stable and flakey
#   a) stable tests - run them first
#   b) flakey tests - run them after the stable tests
class ESTestSuiteFile(pytest.File): 
    def get_filter_status(self, test_name: str) -> bool:
        # if it is flakey, return False
        # else return True
        if GREEN_MODE:
            # TODO external server to be replaced with 
            # http://ghost-test-api.engr.akamai.com/results/
            try:
                logger.info(f"Checking status for {test_name}")
                test_status = requests.get(EXTERNAL_SERVER).text
                if test_status == 'FALSE':
                    return False
            except Exception as e:
                logger.warning(f"Failed to check {EXTERNAL_SERVER}: {e}")
            
        return True
    
    def collect(self):
        # read all the tests in the test list 
        # before calling the the wrapper for ESTest, rearrange the test list order
        # based on how stable it is
        logger.info(f"Starting to split {TEST_LIST_FILE} into stable and flakey tests")
        flakey_test_file_handle, flakey_test_path = tempfile.mkstemp(
            '.list', 'crypto_flakey_tests_', '.', text=True)
        stable_test_file_handle, stable_test_path = tempfile.mkstemp(
            '.list', 'crypto_stable_tests_', '.', text=True)
        
        with open(TEST_LIST_FILE) as fd:
            for test_name in fd:
                if test_name.strip().startswith('#'):
                    # skip comments
                    continue
                get_status = self.get_filter_status(test_name)
                file_handle = flakey_test_file_handle
                if get_status:
                    file_handle = stable_test_file_handle
                os.write(file_handle, test_name.encode('utf-8'))

        os.close(flakey_test_file_handle)
        os.close(stable_test_file_handle)
        logger.info(
            f"Finished splitting test list into {stable_test_path} and {flakey_test_path} tests")
        # run the stable tests and then the flakey tests   
        yield ESTestItem.from_parent(
            self, 
            stable_tests=stable_test_path, 
            flakey_tests=flakey_test_path)


class ESTestHook:
    def __init__(self):
        tmpfile, tmpname = tempfile.mkstemp('.pl', 'tmp_', '.', text=True)
        self.estest_wrapper_handle = tmpfile
        self.estest_wrapper = tmpname
    
    def call_estest(self, stable_list, flakey_list):
        stable_list = stable_list.split("/")[-1]
        flakey_list = flakey_list.split("/")[-1]

        def get_test_lists():
            final_list = f'tests/root/cryptoserver/{stable_list}'
            final_list += ' '
            final_list += f'tests/root/cryptoserver/{flakey_list}'
            return final_list
        
        def start_estest(*args):
            tests_to_run = "%s" % (get_test_lists())
            logger.info(f"Running ESTest with test list: {tests_to_run}")
            os.write(self.estest_wrapper_handle, (r'''#!/usr/bin/perl
{
    local @ARGV = ("-root", "-log_sections=all", "-logdir=logs/", "%s");
    do '%s';
}
''' % (tests_to_run, ESTEST_FILE)).encode('utf-8'))

            os.close(self.estest_wrapper_handle)
            os.chmod(self.estest_wrapper, stat.S_IXUSR)
            retcode = subprocess.call(
                "%s" % (self.estest_wrapper), 
                shell=True, 
#                timeout=SUITE_TIMEOUT,
                stderr=subprocess.PIPE, 
                stdout=subprocess.PIPE)
            self.return_code = retcode
            if not DEBUG:
                os.remove(self.estest_wrapper)
        start_estest()


class ESTestItem(pytest.Item):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stable_tests = kwargs.get('stable_tests')
        self.flakey_tests = kwargs.get('flakey_tests')

    def runtest(self):
        obj_ = ESTestHook()
        obj_.call_estest(self.stable_tests, self.flakey_tests)
        if obj_.return_code:
            raise ESTestFailureException(self)

    def repr_failure(self, excinfo):
        """Called when self.runtest() raises an exception.""" 
        if isinstance(excinfo.value, ESTestFailureException):
            return "\n".join(["execution failed"])
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"test lists: {self.stable_tests} {self.flakey_tests}"


class ESTestFailureException(Exception):
    """Custom exception for error reporting."""
