#!/usr/bin/env python
# -*- coding: utf-8 -*-
import codecs
import os
from setuptools import setup
from setuptools.command.test import test as TestCommand


class PyTest(TestCommand):
    def __init__(self, *args, **kwargs):
        TestCommand.__init__(self, *args, **kwargs)
        self.test_suite = True

    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []

    def run_tests(self):
        # import here, cause outside the eggs aren't loaded
        import pytest
        errno = pytest.main(self.test_args)
        raise SystemExit(errno)


with codecs.open('README.rst', encoding='utf8') as readme_file:
    README = readme_file.read()

with codecs.open('HISTORY.rst', encoding='utf8') as history_file:
    HISTORY = history_file.read().replace('.. :changelog:', '')

LONG_DESCRIPTION = README + '\n\n' + HISTORY

# *IMPORTANT*: Don't manually change the version here. Use the 'bump2version' utility.
# see: https://github.com/ethereum/pyethapp/wiki/Development:-Versions-and-Releases
version = '1.5.1a0'

setup(
    name='pyethapp',
    version=version,
    description='Python Ethereum Client',
    long_description=LONG_DESCRIPTION,
    author='HeikoHeiko',
    author_email='heiko@ethdev.com',
    url='https://github.com/ethereum/pyethapp',
    packages=[
        'pyethapp',
    ],
    package_data={
        'pyethapp': ['genesisdata/*.json']
    },
    license='MIT',
    zip_safe=False,
    keywords='pyethapp',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.6',
    ],
    cmdclass={'test': PyTest},
    tests_require=[
        'mock==2.0.0',
        'pytest-mock==1.6.0',
    ],
    entry_points='''
    [console_scripts]
    pyethapp=pyethapp.app:app
    '''
)
