"""
Pytest hook to collect test files.

This function is called by pytest to determine if a given file should be collected as a test suite.
If the file has a `.list` extension and its name starts with the value of `TEST_LIST_FILE`, it is
recognized as an ESTest suite file and an `ESTestSuiteFile` instance is returned for collection.

Args:
    parent: The parent collector node.
    file_path: The path to the file being considered for collection.

Returns:
    ESTestSuiteFile: An instance of ESTestSuiteFile if the file matches the 
    criteria, otherwise None.
"""
from __future__ import annotations

import logging
import os
import stat
import subprocess
import tempfile
import pytest #pylint: disable=import-error
import requests #pylint: disable=import-error


DEBUG = True
ESTEST_FILE = './ESTest.pl'
EXTERNAL_SERVER = "http://localhost:8000/"
GREEN_MODE = os.getenv('GREEN_MODE', 'True').lower() == 'true'  # Read from ENV, default to True
#SUITE_TIMEOUT = 1000
TEST_LIST_FILE = 'crypto_tests.list'
TEST_LIST_PATH = 'tests/root/cryptoserver/'

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.WARNING)
logger = logging.getLogger(__name__)


def pytest_collect_file(parent, file_path):
    """
    Pytest hook to collect custom test suite files during test discovery.

    This function checks if the given file has a `.list` extension and its name starts with
    the value of `TEST_LIST_FILE`. If both conditions are met, it logs the discovery and
    returns an `ESTestSuiteFile` collector for the file, enabling pytest to collect tests
    from custom suite files.

    Args:
        parent: The parent collector node in the pytest collection tree.
        file_path: The path to the file being considered for collection.

    Returns:
        An instance of `ESTestSuiteFile` if the file matches the criteria, otherwise `None`.
    """
    if file_path.suffix == ".list" and file_path.name.startswith(TEST_LIST_FILE):
        logger.info("Found ESTest suite file %s in %s", TEST_LIST_FILE, file_path)
        #globals()['TEST_LIST_PATH'] = str(file_path)
        return ESTestSuiteFile.from_parent(parent=parent, fspath=file_path)


"""
Split the test list into two parts - stable and flakey
stable tests - run them first
flakey tests - run them after the stable tests
"""
class ESTestSuiteFile(pytest.File):
    def get_filter_status(self, test_name: str) -> bool:
        """
        if it is flakey, return False
        """
        if GREEN_MODE:
            try:
                logger.info("Checking status for %s", test_name.strip())
                test_status = requests.get(EXTERNAL_SERVER).json()
                if not test_status.get('result'):
                    return False
            except Exception as e:
                logger.warning("Failed to check %s: %s", EXTERNAL_SERVER, str(e))
                logger.warning("Assuming test is stable")

        return True

    def collect(self):
        """
        read all the tests in the test list
        before calling the the wrapper for ESTest, rearrange the test list order
        """
        logger.debug("Starting to split into stable and flakey tests")
        flakey_test_file_handle, flakey_test_path = tempfile.mkstemp(
            '.list', 'crypto_flakey_tests_', '.', text=True)
        stable_test_file_handle, stable_test_path = tempfile.mkstemp(
            '.list', 'crypto_stable_tests_', '.', text=True)

        with open(TEST_LIST_PATH + TEST_LIST_FILE) as fd:
            for test_name in fd:
                if test_name.strip().startswith('#'):
                    # skip comments
                    continue
                status = self.get_filter_status(test_name)
                file_handle = flakey_test_file_handle
                if status:
                    file_handle = stable_test_file_handle
                os.write(file_handle, test_name.encode('utf-8'))

        os.close(flakey_test_file_handle)
        os.close(stable_test_file_handle)
        # Move these files to tests/root/cryptoserver
        os.rename(stable_test_path, f'{TEST_LIST_PATH}/crypto_stable_tests.list')
        os.rename(flakey_test_path, f'{TEST_LIST_PATH}/crypto_flakey_tests.list')
        logger.debug(
            "Finished splitting test list into stable: %s and flakey: %s tests",
            stable_test_path, flakey_test_path
        )
        # run the stable and flakey tests
        yield ESTestItem.from_parent(
            self,
            stable_tests=f'{TEST_LIST_PATH}/crypto_stable_tests.list',
            flakey_tests=f'{TEST_LIST_PATH}/crypto_flakey_tests.list')


"""
Custom pytest item to run ESTest with stable and flakey tests.
This class defines a pytest item that executes the ESTest.pl script with the 
provided stable and flakey test lists.
"""
class ESTestHook:
    def __init__(self):
        tmpfile, tmpname = tempfile.mkstemp('.pl', 'tmp_', '.', text=True)
        self.estest_wrapper_handle = tmpfile
        self.estest_wrapper = tmpname
        self.return_code = 0

    def call_estest(self, stable_list, flakey_list):
        """
        Call the ESTest.pl script with the stable and flakey test lists.
        """
        def get_test_lists():
            return f'{stable_list}', f'{flakey_list}'

        def start_estest():
            stable_test, flakey_test = get_test_lists()
            os.write(self.estest_wrapper_handle, (r'''#!/usr/bin/perl
{
    local @ARGV = ("-root", "-log_sections=all", "-logdir=logs/", "%s", "%s");
    do '%s';
}
''' % (stable_test, flakey_test, ESTEST_FILE)).encode('utf-8'))

            os.close(self.estest_wrapper_handle)
            os.chmod(self.estest_wrapper, stat.S_IXUSR)
            retcode = subprocess.call(
                "%s" % (self.estest_wrapper),
                shell=True,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE)
            self.return_code = retcode
            if not DEBUG:
                os.remove(self.estest_wrapper)

        start_estest()

"""
This class defines a pytest item that executes the ESTest.pl script with 
the provided stable and flakey test lists.
"""
class ESTestItem(pytest.Item):
    def __init__(self, **kwargs):
        super().__init__(name='Crypto', parent=kwargs.get('parent'))
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
        msg = f"test lists: {self.stable_tests} {self.flakey_tests}"
        return self.path, 0, msg

"""
Custom exception for ESTest failures.
"""
class ESTestFailureException(Exception):
    def __init__(self, item):
        """
        Initialize the exception with the item that caused the failure.
        Args:
            item: The pytest item that caused the ESTest failure.
        """
        super().__init__(f"ESTest failed for item: {item.name}")
        self.item = item
