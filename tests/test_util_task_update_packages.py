# -*- coding: utf-8 -*-

# This file is part of python-opsi.
# Copyright (C) 2018-2019 uib GmbH <info@uib.de>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Testing the opsi-package-updater functionality.

:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

from __future__ import absolute_import

import formatter
import os.path
import shutil

import pytest

from OPSI.Util.Task.UpdatePackages import OpsiPackageUpdater
from OPSI.Util.Task.UpdatePackages.Config import DEFAULT_CONFIG
from OPSI.Util.Task.UpdatePackages.Repository import ProductRepositoryInfo, LinksExtractor

from .helpers import mock, createTemporaryTestfile, workInTemporaryDirectory
from .test_hosts import getConfigServer


@pytest.fixture
def packageUpdaterClass(backendManager):
	configServer = getConfigServer()
	backendManager.host_insertObject(configServer)

	klass = OpsiPackageUpdater
	with mock.patch.object(klass, 'getConfigBackend', return_value=backendManager):
		yield klass


def testListingLocalPackages(packageUpdaterClass):
	with workInTemporaryDirectory() as tempDir:
		configFile = os.path.join(tempDir, 'emptyconfig.conf')
		with open(configFile, 'w'):
			pass

		filenames = [
			'not.tobefound.opsi.nono',
			'thingy_1.2-3.opsi', 'thingy_1.2-3.opsi.no'
		]

		for filename in filenames:
			with open(os.path.join(tempDir, filename), 'w'):
				pass

		config = DEFAULT_CONFIG.copy()
		config['packageDir'] = tempDir
		config['configFile'] = configFile

		packageUpdater = packageUpdaterClass(config)
		localPackages = packageUpdater.getLocalPackages()
		packageInfo = localPackages.pop()
		assert not localPackages, "There should only be one package!"

		expectedInfo = {
			"productId": "thingy",
			"version": "1.2-3",
			"packageFile": os.path.join(tempDir, 'thingy_1.2-3.opsi'),
			"filename": "thingy_1.2-3.opsi",
			"md5sum": None
		}

		assert set(packageInfo.keys()) == set(expectedInfo.keys())
		assert packageInfo['md5sum']  # We want any value

		del expectedInfo['md5sum']  # Not comparing this
		for key, expectedValue in expectedInfo.items():
			assert packageInfo[key] == expectedValue


@pytest.fixture
def exampleConfigPath():
	filePath = os.path.join(
		os.path.dirname(__file__), 'testdata', 'util', 'task',
		'updatePackages', 'example_updater.conf'
	)
	with createTemporaryTestfile(filePath) as newPath:
		yield newPath


def testParsingConfigFile(exampleConfigPath, packageUpdaterClass):
	with workInTemporaryDirectory() as tempDir:
		preparedConfig = DEFAULT_CONFIG.copy()
		preparedConfig['packageDir'] = tempDir
		preparedConfig['configFile'] = exampleConfigPath

		repoPath = os.path.join(tempDir, 'repos.d')
		os.mkdir(repoPath)

		patchConfigFile(exampleConfigPath, packageDir=tempDir, repositoryConfigDir=repoPath)
		copyExampleRepoConfigs(repoPath)

		packageUpdater = packageUpdaterClass(preparedConfig)
		config = packageUpdater.config

		assert config
		assert config['repositories']
		assert len(config['repositories']) == 3
		for repo in config['repositories']:
			assert isinstance(repo, ProductRepositoryInfo)

		assert config['packageDir'] == tempDir
		assert config['tempdir'] == '/tmp'
		assert config['repositoryConfigDir'] == repoPath

		# Global proxy
		assert not config['proxy']

		# e-mail notification settings
		assert config['notification'] == False
		assert config['smtphost'] == 'smtp'
		assert config['smtpport'] == 25
		assert config['smtpuser'] == DEFAULT_CONFIG['smtpuser']
		assert config['smtppassword'] == DEFAULT_CONFIG['smtppassword']
		assert config['use_starttls'] == False
		assert config['sender'] == 'opsi-package-updater@localhost'
		assert config['receivers'] == ['root@localhost', 'anotheruser@localhost']
		assert config['subject'] == 'opsi-package-updater example config'

		# Automatic installation settings
		assert config['installationWindowStartTime'] == '01:23'
		assert config['installationWindowEndTime'] == '04:56'
		assert config['installationWindowExceptions'] == [u'firstproduct', u'second-product']

		# Wake-On-LAN settings
		assert config['wolAction'] == False
		assert config['wolActionExcludeProductIds'] == ['this', 'that']
		assert config['wolShutdownWanted'] == True
		assert config['wolStartGap'] == 10


def patchConfigFile(filename, **values):
	with open(filename) as configFile:
		lines = configFile.readlines()

	newLines = []
	for line in lines:
		for key, value in values.items():
			if line.startswith(key):
				newLines.append('{} = {}\n'.format(key, value))
				break
		else:
			newLines.append(line)

	with open(filename, 'w') as configFile:
		for line in newLines:
			configFile.write(line)


def copyExampleRepoConfigs(targetDir):
	for filename in ('experimental.repo', ):
		filePath = os.path.join(
			os.path.dirname(__file__), 'testdata', 'util', 'task',
			'updatePackages', filename
		)
		shutil.copy(filePath, targetDir)


@pytest.fixture(
	params=['apachelisting.html'],
	ids=['apache']
)
def repositoryListingPage(request):
	filePath = os.path.join(
		os.path.dirname(__file__), 'testdata', 'util', 'task',
		'updatePackages', request.param
	)

	with open(filePath) as exampleFile:
		return exampleFile.read()


def testLinkExtracting(repositoryListingPage):
	defaultFormatter = formatter.NullFormatter()
	extractor = LinksExtractor(defaultFormatter)
	extractor.feed(repositoryListingPage)
	extractor.close()

	for link in extractor.getLinks():
		# Currently just checking their existance
		break
	else:
		raise RuntimeError("No links found!")


def testGlobalProxyAppliedToRepos(exampleConfigPath, packageUpdaterClass):
	testProxy = 'http://hurr:durr@someproxy:1234'

	with workInTemporaryDirectory() as tempDir:
		preparedConfig = DEFAULT_CONFIG.copy()
		preparedConfig['packageDir'] = tempDir
		preparedConfig['configFile'] = exampleConfigPath

		repoPath = os.path.join(tempDir, 'repos.d')
		os.mkdir(repoPath)

		patchConfigFile(exampleConfigPath, packageDir=tempDir, repositoryConfigDir=repoPath, proxy=testProxy)
		copyExampleRepoConfigs(repoPath)

		packageUpdater = packageUpdaterClass(preparedConfig)
		config = packageUpdater.config

		assert config['proxy'] == testProxy

		for repo in config['repositories']:
			print(repo.active)
			assert repo.proxy == testProxy
