# -*- encoding: utf-8 -*-
""" Setup for EsiPy """
import io
from setuptools import setup

import esipy

# install requirements
install_requirements = [
    "requests",
    "openapi-core >= 0.19.0",
    "pytz",
    "python-jose >= 3.0 , < 4"
]

# test requirements
test_requirements = [
    "coverage",
    "coveralls",
    "httmock",
    "nose",
    "python-memcached",
    "redis",
    "diskcache",
] + install_requirements

with io.open('README.rst', encoding='UTF-8') as reader:
    README = reader.read()

setup(
    name='EsiPy',
    version=esipy.__version__,
    packages=['esipy'],
    url='https://github.com/Kyria/EsiPy',
    license='BSD 3-Clause License',
    author='Kyria',
    author_email='anakhon@gmail.com',
    description='Swagger Client for the ESI API for EVE Online',
    long_description=README,
    install_requires=install_requirements,
    tests_require=test_requirements,
    test_suite='nose.collector',
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: Implementation :: CPython",
    ],
    python_requires=">=3.8"
)
