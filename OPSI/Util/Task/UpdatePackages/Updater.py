# -*- coding: utf-8 -*-

# This module is part of the desktop management solution opsi
# (open pc server integration) http://www.opsi.org

# Copyright (C) 2018 uib GmbH - http://www.uib.de/

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
Components for updating packages.

:copyright: uib GmbH <info@uib.de>
:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

from __future__ import absolute_import

import formatter
import os
import os.path
import socket
import time
import urllib
import urllib2

from .Exceptions import ConfigurationError, MissingConfigurationValueError
from .Notifier import EmailNotifier
from .Repository import getRepoConfigs, LinksExtractor, ProductRepositoryInfo

from OPSI import System
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Logger import LOG_DEBUG, Logger
from OPSI.Object import NetbootProduct, ProductOnClient
from OPSI.Types import (
	forceBool, forceEmailAddress, forceFilename, forceHostAddress,
	forceHostId, forceInt, forceProductId, forceUnicode, forceUrl)
from OPSI.Util import compareVersions, formatFileSize, getfqdn, md5sum
from OPSI.Util.File import IniFile, ZsyncFile
from OPSI.Util.File.Opsi import parseFilename
from OPSI.Util.Product import ProductPackageFile
from OPSI.Util.Task.Rights import setRights

__all__ = ('OpsiPackageUpdater', )

logger = Logger()


def splitAndStrip(string, sep):
	for singleValue in string.split(sep):
		singleValue = singleValue.strip()
		if singleValue:
			yield singleValue


class OpsiPackageUpdater(object):
	def __init__(self, config):
		self.config = config
		self.httpHeaders = {'User-Agent': self.config.get("userAgent", "")}
		self.configBackend = None
		self.depotConnections = {}
		self.depotId = forceHostId(getfqdn(conf='/etc/opsi/global.conf').lower())
		self.errors = []

		depots = self.getConfigBackend().host_getObjects(type='OpsiDepotserver', id=self.depotId)
		try:
			self.depotKey = depots[0].opsiHostKey
		except IndexError:
			raise ValueError(u"Depot '%s' not found in backend" % self.depotId)

		if not self.depotKey:
			raise ValueError(u"Opsi host key for depot '%s' not found in backend" % self.depotId)
		logger.addConfidentialString(self.depotKey)

		self.readConfigFile()

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		for con in self.depotConnections.values():
			try:
				con.backend_exit()
			except Exception:
				pass

		try:
			self.configBackend.backend_exit()
		except Exception:
			pass

	def getActiveRepositories(self):
		"""
		Iterates over the found repositories and yields the active ones
		one by one.
		If a repository name filter is given only repositories matching
		the name will be returned.

		:rtype: ProductRepositoryInfo
		"""
		for repo in self.getRepositories():
			if not repo.active:
				continue

			yield repo

	def getRepositories(self):
		"""
		Iterates over all found repositories and yields them.
		If a repository name filter is given only repositories matching
		the name will be returned.

		:rtype: ProductRepositoryInfo
		"""
		name = self.config.get("repositoryName", None)

		for repo in self.config.get('repositories', []):
			if name and repo.name.strip().lower() != name.strip().lower():
				continue

			yield repo

	def readConfigFile(self):
		try:
			logger.info(u"Reading config file '%s'" % self.config["configFile"])
			if not os.path.isfile(self.config["configFile"]):
				raise OSError(u"Configuration file {!r} not found".format(self.config["configFile"]))

			self.config['repositories'] = []

			iniFile = IniFile(filename=self.config['configFile'], raw=True)
			config = iniFile.parse()
			for section in config.sections():
				if section.lower() == 'general':
					for (option, value) in config.items(section):
						if option.lower() == 'packagedir':
							self.config["packageDir"] = forceFilename(value.strip())
						elif option.lower() == 'logfile':
							value = forceFilename(value.strip())
							logger.setLogFile(value)
						elif option.lower() == 'loglevel':
							logger.setFileLevel(forceInt(value.strip()))
						elif option.lower() == 'timeout':
							socket.setdefaulttimeout(float(value.strip()))
						elif option.lower() == 'tempdir':
							self.config["tempdir"] = value.strip()
						elif option.lower() == 'repositoryconfigdir':
							self.config["repositoryConfigDir"] = value.strip()

				elif section.lower() == 'notification':
					for (option, value) in config.items(section):
						if option.lower() == 'active':
							self.config["notification"] = forceBool(value)
						elif option.lower() == 'smtphost':
							self.config["smtphost"] = forceHostAddress(value.strip())
						elif option.lower() == 'smtpport':
							self.config["smtpport"] = forceInt(value.strip())
						elif option.lower() == 'smtpuser':
							self.config["smtpuser"] = forceUnicode(value.strip())
						elif option.lower() == 'smtppassword':
							self.config["smtppassword"] = forceUnicode(value.strip())
						elif option.lower() == 'subject':
							self.config["subject"] = forceUnicode(value.strip())
						elif option.lower() == 'use_starttls':
							self.config["use_starttls"] = forceBool(value.strip())
						elif option.lower() == 'sender':
							self.config["sender"] = forceEmailAddress(value.strip())
						elif option.lower() == 'receivers':
							self.config["receivers"] = []

							for receiver in splitAndStrip(value, u","):
								self.config["receivers"].append(forceEmailAddress(receiver))

				elif section.lower() == 'wol':
					for (option, value) in config.items(section):
						if option.lower() == 'active':
							self.config["wolAction"] = forceBool(value.strip())
						elif option.lower() == 'excludeproductids':
							self.config['wolActionExcludeProductIds'] = []

							for productId in splitAndStrip(value, u','):
								self.config["wolActionExcludeProductIds"].append(forceProductId(productId))
						elif option.lower() == 'shutdownwanted':
							self.config["wolShutdownWanted"] = forceBool(value.strip())
						elif option.lower() == 'startgap':
							self.config["wolStartGap"] = forceInt(value.strip())
							if self.config["wolStartGap"] < 0:
								self.config["wolStartGap"] = 0

				elif section.lower() == 'installation':
					for (option, value) in config.items(section):
						if option.lower() == 'windowstart':
							if not value.strip():
								continue
							if not re.search('^\d{1,2}\:\d{1,2}$', value.strip()):
								raise ValueError(u"Start time '%s' not in needed format 'HH:MM'" % value.strip())
							self.config["installationWindowStartTime"] = value.strip()
						elif option.lower() == 'windowend':
							if not value.strip():
								continue
							if not re.search('^\d{1,2}\:\d{1,2}$', value.strip()):
								raise ValueError(u"End time '%s' not in needed format 'HH:MM'" % value.strip())
							self.config["installationWindowEndTime"] = value.strip()
						elif option.lower() == 'exceptproductids':
							self.config['installationWindowExceptions'] = []

							for productId in splitAndStrip(value, ','):
								self.config["installationWindowExceptions"].append(forceProductId(productId))

				elif section.lower().startswith('repository'):
					try:
						repository = self._getRepository(config, section)
						self.config['repositories'].append(repository)
					except MissingConfigurationValueError as mcverr:
						logger.debug(u"Configuration for {section} incomplete: {error}", error=mcverr, section=section)
					except ConfigurationError as cerr:
						logger.error(u"Configuration problem in {section}: {error}", error=cerr, section=section)
					except Exception as err:
						logger.error(u"Can't load repository from {section}: {error}", error=err, section=section)
				else:
					logger.error(u"Unhandled section '%s'" % section)
		except Exception as exclude:
			raise RuntimeError(u"Failed to read config file '%s': %s" % (self.config["configFile"], exclude))

		for configFile in getRepoConfigs(self.config['repositoryConfigDir']):
			iniFile = IniFile(filename=configFile, raw=True)

			try:
				config = iniFile.parse()
				for section in config.sections():
					if not section.lower().startswith('repository'):
						continue

					try:
						repository = self._getRepository(config, section)
						self.config['repositories'].append(repository)
					except MissingConfigurationValueError as mcverr:
						logger.debug(u"Configuration for {section} in {filename} incomplete: {error}", error=mcverr, section=section, filename=configFile)
					except ConfigurationError as cerr:
						logger.error(u"Configuration problem in {section} in {filename}: {error}", error=cerr, section=section, filename=configFile)
					except Exception as err:
						logger.error(u"Can't load repository from {section} in {filename}: {error}", error=err, section=section, filename=configFile)
			except Exception as error:
				logger.error("Unable to load repositories from {filename}: {error}", filename=configFile, error=error)

	def _getRepository(self, config, section):
		active = False
		baseUrl = None
		opsiDepotId = None
		proxy = None
		for (option, value) in config.items(section):
			option = option.lower()
			value = value.strip()
			if option == 'active':
				active = forceBool(value)
			elif option == 'baseurl':
				if value:
					baseUrl = forceUrl(value)
			elif option == 'opsidepotid':
				if value:
					opsiDepotId = forceHostId(value)
			elif option == 'proxy':
				if value:
					proxy = forceUrl(value)

		repoName = section.replace('repository_', '', 1)

		if self.config['forceRepositoryActivation']:
			if repoName == self.config['repositoryName']:
				logger.debug("Activation for repository {0} forced.", repoName)
				active = True
			else:
				active = False

		repository = None
		if opsiDepotId:
			depots = self.getConfigBackend().host_getObjects(type='OpsiDepotserver', id=opsiDepotId)
			if not depots:
				raise ConfigurationError(u"Depot '%s' not found in backend" % opsiDepotId)
			if not depots[0].repositoryRemoteUrl:
				raise ConfigurationError(u"Repository remote url for depot '%s' not found in backend" % opsiDepotId)

			repository = ProductRepositoryInfo(
				name=repoName,
				baseUrl=depots[0].repositoryRemoteUrl,
				dirs=['/'],
				username=self.depotId,
				password=self.depotKey,
				opsiDepotId=opsiDepotId,
				active=active
			)

		elif baseUrl:
			if proxy:
				logger.notice(u"Using Proxy: %s" % proxy)

			repository = ProductRepositoryInfo(
				name=repoName,
				baseUrl=baseUrl,
				proxy=proxy,
				active=active
			)
		else:
			raise MissingConfigurationValueError(u"Repository section '{0}': neither baseUrl nor opsiDepotId set".format(section))

		for (option, value) in config.items(section):
			if option.lower() == 'username':
				repository.username = forceUnicode(value.strip())
			elif option.lower() == 'password':
				repository.password = forceUnicode(value.strip())
				if repository.password:
					logger.addConfidentialString(repository.password)
			elif option.lower() == 'autoinstall':
				repository.autoInstall = forceBool(value.strip())
			elif option.lower() == 'autoupdate':
				repository.autoUpdate = forceBool(value.strip())
			elif option.lower() == 'autosetup':
				repository.autoSetup = forceBool(value.strip())
			elif option.lower() == 'onlydownload':
				repository.onlyDownload = forceBool(value.strip())
			elif option.lower() == 'inheritproductproperties':
				if not opsiDepotId:
					logger.warning(u"InheritProductProperties not possible with normal http ressource.")
					repository.inheritProductProperties = False
				else:
					repository.inheritProductProperties = forceBool(value.strip())
			elif option.lower() == 'dirs':
				repository.dirs = []
				for directory in splitAndStrip(value, ','):
					repository.dirs.append(forceFilename(directory))
			elif option.lower() == 'excludes':
				repository.excludes = []
				for exclude in splitAndStrip(value, ','):
					repository.excludes.append(re.compile(exclude))
			elif option.lower() == 'includeproductids':
				repository.includes = []
				for include in splitAndStrip(value, ','):
					repository.includes.append(re.compile(include))
			elif option.lower() == 'description':
				repository.description = forceUnicode(value)

		if self.config.get('installAllAvailable'):
			repository.autoInstall = True
			repository.autoUpdate = True
			repository.excludes = []

		return repository

	def getConfigBackend(self):
		if not self.configBackend:
			self.configBackend = BackendManager(
				dispatchConfigFile=u'/etc/opsi/backendManager/dispatch.conf',
				backendConfigDir=u'/etc/opsi/backends',
				extensionConfigDir=u'/etc/opsi/backendManager/extend.d',
				depotbackend=True,
				hostControlBackend=True
			)
		return self.configBackend

	def getDepotConnection(self, depotId, username, password):
		if depotId not in self.depotConnections:
			self.depotConnections[depotId] = JSONRPCBackend(
				address=depotId,
				username=username,
				password=password,
				application=self.httpHeaders['User-Agent'] + ' (depot connection)',
			)
		return self.depotConnections[depotId]

	def processUpdates(self):
		if not any(self.getActiveRepositories()):
			logger.warning(u"No repositories configured, nothing to do")
			return

		notifier = None
		if self.config["notification"]:
			logger.info(u"Notification is activated")
			notifier = self._getNotifier()

		try:
			try:
				installedProducts = self.getInstalledProducts()
				localPackages = self.getLocalPackages()
				downloadablePackages = self.getDownloadablePackages()
				downloadablePackages = self.onlyNewestPackages(downloadablePackages)
				downloadablePackages = self._filterProducts(downloadablePackages)

				newPackages = []
				for availablePackage in downloadablePackages:
					logger.info(u"Testing if download/installation of package '{0}' is needed", availablePackage["filename"])
					productInstalled = False
					updateAvailable = False
					for product in installedProducts:
						if product['productId'] == availablePackage['productId']:
							logger.debug(u"Product '{0}' is installed", availablePackage['productId'])
							productInstalled = True
							logger.debug(
								u"Available product version is '{0}', installed product version is '{1}-{2}'",
								availablePackage['version'], product['productVersion'], product['packageVersion']
							)
							updateAvailable = compareVersions(availablePackage['version'], '>', '%s-%s' % (product['productVersion'], product['packageVersion']))
							break

					installationRequired = False
					if not productInstalled:
						if availablePackage['repository'].autoInstall:
							logger.notice(
								u"{0} - installation required: product '{1}' is not installed and auto install is set for repository '{2}'",
								availablePackage["filename"], availablePackage['productId'], availablePackage['repository'].name
							)
							installationRequired = True
						else:
							logger.info(
								u"{0} - installation not required: product '{1}' is not installed but auto install is not set for repository '{2}'",
								availablePackage["filename"], availablePackage['productId'], availablePackage['repository'].name
							)
					elif updateAvailable:
						if availablePackage['repository'].autoUpdate:
							logger.notice(
								u"{0} - installation required: a more recent version of product '{1}' was found (installed: {2}-{3}, available: {4}) and auto update is set for repository '{5}'",
								availablePackage["filename"], availablePackage['productId'], product['productVersion'], product['packageVersion'], availablePackage['version'], availablePackage['repository'].name
							)
							installationRequired = True
						else:
							logger.info(
								u"{0} - installation not required: a more recent version of product '{1}' was found (installed: {2}-{3}, available: {4}) but auto update is not set for repository '{5}'",
								availablePackage["filename"], availablePackage['productId'], product['productVersion'], product['packageVersion'], availablePackage['version'], availablePackage['repository'].name
							)
					else:
						logger.info(
							u"{0} - installation not required: installed version '{1}-{2}' of product '{3}' is up to date",
							availablePackage["filename"], product['productVersion'], product['packageVersion'], availablePackage['productId']
						)

					if not installationRequired:
						continue

					downloadNeeded = True
					localPackageFound = None
					for localPackage in localPackages:
						if localPackage['productId'] == availablePackage['productId']:
							logger.debug(u"Found local package file '%s'" % localPackage['filename'])
							localPackageFound = localPackage
							if localPackage['filename'] == availablePackage['filename']:
								if localPackage['md5sum'] == availablePackage['md5sum']:
									downloadNeeded = False
									break
					if not downloadNeeded:
						logger.info(
							u"{0} - download of package is not required: found local package {1} with matching md5sum",
							availablePackage["filename"], localPackageFound['filename']
						)
					elif localPackageFound:
						logger.info(
							u"{0} - download of package is required: found local package {1} which differs from available",
							availablePackage["filename"], localPackageFound['filename']
						)
					else:
						logger.info(u"{0} - download of package is required: local package not found", availablePackage["filename"])

					packageFile = os.path.join(self.config["packageDir"], availablePackage["filename"])
					zsynced = False
					if downloadNeeded:
						if self.config["zsyncCommand"] and availablePackage['zsyncFile'] and localPackageFound:
							if availablePackage['repository'].baseUrl.split(':')[0].lower().endswith('s'):
								logger.warning(u"Cannot use zsync, because zsync does not support https")
								self.downloadPackage(availablePackage, notifier=notifier)
							else:
								if localPackageFound['filename'] != availablePackage['filename']:
									os.rename(os.path.join(self.config["packageDir"], localPackageFound["filename"]), packageFile)
									localPackageFound["filename"] = availablePackage['filename']
								self.zsyncPackage(availablePackage, notifier=notifier)
								zsynced = True
						else:
							self.downloadPackage(availablePackage, notifier=notifier)
						self.cleanupPackages(availablePackage)

					if availablePackage['md5sum']:
						logger.info(u"Verifying download of package '%s'" % packageFile)
						md5 = md5sum(packageFile)
						if md5 == availablePackage["md5sum"]:
							logger.info(u"{productId}: md5sum match, package download verified", productId=availablePackage['productId'])
						elif md5 != availablePackage["md5sum"] and zsynced:
							logger.warning(u"{productId}: zsynced Download has failed, try once to load full package", productId=availablePackage['productId'])
							self.downloadPackage(availablePackage, notifier=notifier)
							self.cleanupPackages(availablePackage)

							md5 = md5sum(packageFile)
							if md5 == availablePackage["md5sum"]:
								logger.info(u"{productId}: md5sum match, package download verified", productId=availablePackage['productId'])
							else:
								raise RuntimeError(u"Failed to download package '%s', md5sum mismatch" % availablePackage['packageFile'])
						else:
							logger.info(u"{productId}: md5sum mismatch and no zsync. Doing nothing.", productId=availablePackage['productId'])
					else:
						logger.warning(u"{productId}: Cannot verify download of package: missing md5sum file", productId=availablePackage['productId'])

					newPackages.append(availablePackage)

				if not newPackages:
					logger.notice(u"No new packages downloaded")
					return

				now = time.localtime()
				now = '%d:%d' % (now[3], now[4])

				def tdiff(t1, t2):
					t1 = int(t1.split(':')[0]) * 60 + int(t1.split(':')[1])
					t2 = int(t2.split(':')[0]) * 60 + int(t2.split(':')[1])
					if t1 > t2:
						return 24 * 60 - t1 + t2

				insideInstallWindow = True
				if not self.config['installationWindowStartTime'] or not self.config['installationWindowEndTime']:
					logger.info(u"Installation time window not defined, installing products and setting actions")
				elif tdiff(self.config['installationWindowStartTime'], self.config['installationWindowEndTime']) >= tdiff(self.config['installationWindowStartTime'], now):
					logger.notice(u"We are inside the installation time window, installing products and setting actions")
				else:
					logger.notice(u"We are outside installation time window, not installing products except for product ids %s" \
							% self.config['installationWindowExceptions'])
					insideInstallWindow = False

				sequence = []
				for package in newPackages:
					if not insideInstallWindow and not package['productId'] in self.config['installationWindowExceptions']:
						continue
					sequence.append(package['productId'])

				for package in newPackages:
					if not package['productId'] in sequence:
						continue
					packageFile = os.path.join(self.config["packageDir"], package["filename"])
					productId = package['productId']
					ppf = ProductPackageFile(packageFile, tempDir=self.config.get('tempdir', '/tmp'))
					ppf.getMetaData()
					dependencies = ppf.packageControlFile.getPackageDependencies()
					ppf.cleanup()
					for dependency in dependencies:
						try:
							ppos = sequence.index(productId)
							dpos = sequence.index(dependency['package'])
							if ppos < dpos:
								sequence.remove(dependency['package'])
								sequence.insert(ppos, dependency['package'])
						except Exception as error:
							logger.debug(u"While processing package '%s', dependency '%s': %s" % (packageFile, dependency['package'], error))

				sortedPackages = []
				for productId in sequence:
					for package in newPackages:
						if productId == package['productId']:
							sortedPackages.append(package)
							break
				newPackages = sortedPackages

				installedPackages = []
				for package in newPackages:
					packageFile = os.path.join(self.config["packageDir"], package["filename"])

					if package['repository'].onlyDownload:
						continue

					propertyDefaultValues = {}
					try:
						if package['repository'].inheritProductProperties and availablePackage['repository'].opsiDepotId:
							logger.info(u"Trying to get product property defaults from repository")
							productPropertyStates = self.getConfigBackend().productPropertyState_getObjects(
											productId=package['productId'],
											objectId=availablePackage['repository'].opsiDepotId)
						else:
							productPropertyStates = self.getConfigBackend().productPropertyState_getObjects(
											productId=package['productId'],
											objectId=self.depotId)
						if productPropertyStates:
							for pps in productPropertyStates:
								propertyDefaultValues[pps.propertyId] = pps.values
						logger.notice(u"Using product property defaults: %s" % propertyDefaultValues)
					except Exception as error:
						logger.warning(u"Failed to get product property defaults: %s" % error)

					logger.info(u"Installing package '%s'" % packageFile)
					self.getConfigBackend().depot_installPackage(filename=packageFile, force=True, propertyDefaultValues=propertyDefaultValues, tempDir=self.config.get('tempdir', '/tmp'))
					productOnDepots = self.getConfigBackend().productOnDepot_getObjects(depotId=self.depotId, productId=package['productId'])
					if not productOnDepots:
						raise ValueError(u"Product '%s' not found on depot '%s' after installation" % (package['productId'], self.depotId))
					package['product'] = self.getConfigBackend().product_getObjects(
						id=productOnDepots[0].productId,
						productVersion=productOnDepots[0].productVersion,
						packageVersion=productOnDepots[0].packageVersion
					)[0]

					if notifier:
						notifier.appendLine(u"Package '%s' successfully installed" % packageFile, pre='\n')
					logger.notice(u"Package '%s' successfully installed" % packageFile)
					installedPackages.append(package)

				if not installedPackages:
					logger.notice(u"No new packages installed")
					return

				wakeOnLanClients = set()
				shutdownProduct = None
				if self.config['wolAction'] and self.config["wolShutdownWanted"]:
					for product in self.getConfigBackend().productOnDepot_getObjects(depotId=self.depotId, productId='shutdownwanted'):
						shutdownProduct = product
						logger.info(u"Found 'shutdownwanted' product on depot '%s': %s" % (self.depotId, shutdownProduct))
						break
					if not shutdownProduct:
						logger.error(u"Product 'shutdownwanted' not avaliable on depot '%s'" % self.depotId)

				for package in installedPackages:
					if not package['product'].setupScript:
						continue
					if package['repository'].autoSetup:
						if isinstance(package['product'], NetbootProduct):
							logger.info(
								u"Not setting action 'setup' for product '{0}' where installation status 'installed' because auto setup is not allowed for netboot products",
								package['productId']
							)
							continue

						logger.notice(
							u"Setting action 'setup' for product '{0}' where installation status 'installed' because auto setup is set for repository '{1}'",
							package['productId'], package['repository'].name
						)
					else:
						logger.info(
							u"Not setting action 'setup' for product '{0}' where installation status 'installed' because auto setup is not set for repository '{1}'",
							package['productId'], package['repository'].name
						)
						continue

					clientToDepotserver = self.getConfigBackend().configState_getClientToDepotserver(depotIds=[self.depotId])
					clientIds = set(
						ctd['clientId']
						for ctd in clientToDepotserver
						if ctd['clientId']
					)

					if clientIds:
						productOnClients = self.getConfigBackend().productOnClient_getObjects(
							attributes=['installationStatus'],
							productId=package['productId'],
							productType='LocalbootProduct',
							clientId=clientIds,
							installationStatus=['installed'],
						)
						if productOnClients:
							wolEnabled = self.config['wolAction']
							excludedWolProducts = set(self.config['wolActionExcludeProductIds'])

							for poc in productOnClients:
								poc.setActionRequest('setup')
								if wolEnabled and package['productId'] not in excludedWolProducts:
									wakeOnLanClients.add(poc.clientId)

							self.getConfigBackend().productOnClient_updateObjects(productOnClients)
							if notifier:
								notifier.appendLine(u"Product {0} set to 'setup' on clients: {1}".format(package['productId'], ', '.join(sorted(poc.clientId for poc in productOnClients))))

				if wakeOnLanClients:
					logger.notice(u"Powering on clients %s" % wakeOnLanClients)
					if notifier:
						notifier.appendLine(u"Powering on clients: {0}".format(', '.join(sorted(wakeOnLanClients))))

					for clientId in wakeOnLanClients:
						try:
							logger.info(u"Powering on client '%s'" % clientId)
							if self.config["wolShutdownWanted"] and shutdownProduct:
								logger.info(u"Setting shutdownwanted to 'setup' for client '%s'" % clientId)

								self.getConfigBackend().productOnClient_updateObjects(
									ProductOnClient(
										productId=shutdownProduct.productId,
										productType=shutdownProduct.productType,
										productVersion=shutdownProduct.productVersion,
										packageVersion=shutdownProduct.packageVersion,
										clientId=clientId,
										actionRequest='setup'
									)
								)
							self.getConfigBackend().hostControl_start(hostIds=[clientId])
							time.sleep(self.config["wolStartGap"])
						except Exception as error:
							logger.error(u"Failed to power on client '%s': %s" % (clientId, error))
			except Exception as error:
				if notifier:
					notifier.appendLine(u"Error occurred: %s" % error)
				raise
		finally:
			if notifier and notifier.hasMessage():
				notifier.notify()

	def _getNotifier(self):
		notifier = EmailNotifier(
			smtphost=self.config["smtphost"],
			smtpport=self.config["smtpport"],
			sender=self.config["sender"],
			receivers=self.config["receivers"],
			subject=self.config["subject"],
		)

		if self.config["use_starttls"]:
			notifier.useStarttls = self.config["use_starttls"]

		if self.config["smtpuser"] and self.config["smtppassword"] is not None:
			notifier.username = self.config["smtpuser"]
			notifier.password = self.config["smtppassword"]

		return notifier

	def _filterProducts(self, products):
		if self.config["processProductIds"]:
			# Checking if given productIds are available and
			# process only these products
			newProductList = []
			for product in self.config["processProductIds"]:
				for pac in products:
					if product == pac["productId"]:
						newProductList.append(pac)
						break
				else:
					logger.error("Product '{productId}' not found in repository!", productId=product)
					possibleProductIDs = sorted(set(pac["productId"] for pac in products))
					logger.notice("Possible products are: {0}", ', '.join(possibleProductIDs))
					raise ValueError(u"You have searched for a product, which was not found in configured repository: '%s'" % product)

			if newProductList:
				return newProductList

		return products

	def downloadPackages(self):
		if not any(self.getActiveRepositories()):
			logger.warning(u"No repositories configured, nothing to do")
			return

		notifier = None
		if self.config["notification"]:
			logger.info(u"Notification is activated")
			notifier = self._getNotifier()

		forceDownload = self.config["forceDownload"]

		try:
			installedProducts = self.getInstalledProducts()
			localPackages = self.getLocalPackages()
			downloadablePackages = self.getDownloadablePackages()
			downloadablePackages = self.onlyNewestPackages(downloadablePackages)
			downloadablePackages = self._filterProducts(downloadablePackages)

			newPackages = []
			for availablePackage in downloadablePackages:
				logger.info(u"Testing if download/installation of package '%s' is needed" % availablePackage["filename"])
				for product in installedProducts:
					if product['productId'] == availablePackage['productId']:
						logger.debug(u"Product '%s' is installed" % availablePackage['productId'])
						logger.debug(u"Available product version is '%s', installed product version is '%s-%s'" \
							% (availablePackage['version'], product['productVersion'], product['packageVersion']))
						break

				downloadNeeded = True
				localPackageFound = None
				for localPackage in localPackages:
					if localPackage['productId'] == availablePackage['productId']:
						logger.debug(u"Found local package file '%s'" % localPackage['filename'])
						localPackageFound = localPackage
						if localPackage['filename'] == availablePackage['filename']:
							if localPackage['md5sum'] == availablePackage['md5sum']:
								downloadNeeded = False
								break

				if forceDownload:
					downloadNeeded = True

					message = u"{filename} - download of package is forced.".format(**availablePackage)
					logger.notice(message)
					if notifier:
						notifier.appendLine(message)
				elif not downloadNeeded:
					logger.info(u"%s - download of package is not required: found local package %s with matching md5sum" \
								% (availablePackage["filename"], localPackageFound['filename']))
				elif localPackageFound:
					message = u"{filename} - download of package is required: found local package {0} which differs from available".format(localPackageFound['filename'], **availablePackage)
					logger.notice(message)
					if notifier:
						notifier.appendLine(message)
				else:
					message = u"{filename} - download of package is required: local package not found".format(**availablePackage)
					logger.notice(message)
					if notifier:
						notifier.appendLine(message)

				packageFile = os.path.join(self.config["packageDir"], availablePackage["filename"])
				zsynced = False
				if downloadNeeded:
					if self.config["zsyncCommand"] and availablePackage['zsyncFile'] and localPackageFound:
						if availablePackage['repository'].baseUrl.split(':')[0].lower().endswith('s'):
							logger.warning(u"Cannot use zsync, because zsync does not support https")
							self.downloadPackage(availablePackage, notifier=notifier)
						else:
							if localPackageFound['filename'] != availablePackage['filename']:
								os.rename(os.path.join(self.config["packageDir"], localPackageFound["filename"]), packageFile)
								localPackageFound["filename"] = availablePackage['filename']
							self.zsyncPackage(availablePackage, notifier=notifier)
							zsynced = True
					else:
						self.downloadPackage(availablePackage, notifier=notifier)
					self.cleanupPackages(availablePackage)

				if availablePackage['md5sum']:
					logger.info(u"Verifying download of package '%s'" % packageFile)
					md5 = md5sum(packageFile)
					if md5 == availablePackage["md5sum"]:
						logger.info(u"{productId}: md5sum match, package download verified", productId=availablePackage['productId'])
					elif md5 != availablePackage["md5sum"] and zsynced:
						logger.warning(u"{productId}: zsynced Download has failed, try once to load full package", productId=availablePackage['productId'])
						self.downloadPackage(availablePackage, notifier=notifier)
						self.cleanupPackages(availablePackage)

						md5 = md5sum(packageFile)
						if md5 == availablePackage["md5sum"]:
							logger.info(u"{productId}: md5sum match, package download verified", productId=availablePackage['productId'])
						else:
							raise RuntimeError(u"Failed to download package '%s', md5sum mismatch" % availablePackage['packageFile'])
					else:
						logger.warning(u"{productId}: md5sum mismatch and no zsync. Doing nothing.", productId=availablePackage['productId'])
				else:
					logger.warning(u"{productId}: Cannot verify download of package: missing md5sum file", productId=availablePackage['productId'])

				newPackages.append(availablePackage)

			if not newPackages:
				logger.notice(u"No new packages downloaded")
				return
		except Exception as error:
			if notifier:
				notifier.appendLine(u"Error occurred: %s" % error)
			raise
		finally:
			if notifier and notifier.hasMessage():
				notifier.notify()

	def zsyncPackage(self, availablePackage, notifier=None):
		outFile = os.path.join(self.config["packageDir"], availablePackage["filename"])
		curdir = os.getcwd()
		os.chdir(os.path.dirname(outFile))
		try:
			logger.info(u"Zsyncing %s to %s" % (availablePackage["packageFile"], outFile))

			cmd = u"%s -A %s='%s:%s' -o '%s' %s 2>&1" % (
				self.config["zsyncCommand"],
				availablePackage['repository'].baseUrl.split('/')[2].split(':')[0],
				availablePackage['repository'].username,
				availablePackage['repository'].password,
				outFile,
				availablePackage["zsyncFile"]
			)

			if availablePackage['repository'].proxy:
				cmd = u"http_proxy=%s %s" % (availablePackage['repository'].proxy, cmd)

			stateRegex = re.compile('\s([\d\.]+)%\s+([\d\.]+)\skBps(.*)$')
			data = ''
			percent = 0.0
			speed = 0
			handle = System.execute(cmd, getHandle=True)
			while True:
				inp = handle.read(16)
				if not inp:
					handle.close()
					break
				data += inp
				match = stateRegex.search(data)
				if not match:
					continue
				data = match.group(3)
				if (percent == 0) and (float(match.group(1)) == 100):
					continue
				percent = float(match.group(1))
				speed = float(match.group(2)) * 8
				logger.debug(u'Zsyncing %s: %d%% (%d kbit/s)' % (availablePackage["packageFile"], percent, speed))
			if notifier:
				notifier.appendLine(u"Zsync of '%s' completed" % availablePackage["packageFile"])
			logger.info(u"Zsync of '%s' completed" % availablePackage["packageFile"])
		finally:
			os.chdir(curdir)

	def downloadPackage(self, availablePackage, notifier=None):
		url = availablePackage["packageFile"]
		outFile = os.path.join(self.config["packageDir"], availablePackage["filename"])

		passwordManager = urllib2.HTTPPasswordMgrWithDefaultRealm()
		passwordManager.add_password(None, availablePackage['repository'].baseUrl, availablePackage['repository'].username, availablePackage['repository'].password)
		handler = urllib2.HTTPBasicAuthHandler(passwordManager)
		if availablePackage['repository'].proxy:
			logger.notice(u"Using Proxy: %s" % availablePackage['repository'].proxy)
			proxyHandler = urllib2.ProxyHandler({'http': availablePackage['repository'].proxy, 'https': availablePackage['repository'].proxy})
			opener = urllib2.build_opener(proxyHandler, handler)
		else:
			opener = urllib2.build_opener(handler)
		urllib2.install_opener(opener)

		req = urllib2.Request(url, None, self.httpHeaders)
		con = opener.open(req)
		size = int(con.info().get('Content-length', 0))
		if size:
			logger.info(u"Downloading %s (%s MB) to %s" % (url, round(size / (1024.0 * 1024.0), 2), outFile))
		else:
			logger.info(u"Downloading %s to %s" % (url, outFile))

		completed = 0.0
		percent = 0.0
		lastTime = time.time()
		lastCompleted = 0
		lastPercent = 0
		speed = 0

		with open(outFile, 'wb') as out:
			for chunk in iter(lambda: con.read(32768), ''):
				completed += len(chunk)
				out.write(chunk)

				if size > 0:
					try:
						percent = round(100 * completed / size, 1)
						if lastPercent != percent:
							lastPercent = percent
							now = time.time()
							if not speed or (now - lastTime) > 2:
								speed = 8 * int(((completed - lastCompleted) / (now - lastTime)) / 1024)
								lastTime = now
								lastCompleted = completed
							logger.debug(u'Downloading {0}: {1:d}% ({2:d} kbit/s)'.format(url, percent, speed))
					except Exception:
						pass

		if notifier:
			if size:
				notifier.appendLine(u"Download of '%s' completed (~%s)" % (url, formatFileSize(size)))
			else:
				notifier.appendLine(u"Download of '%s' completed" % url)
		logger.info(u"Download of '%s' completed" % url)

	def cleanupPackages(self, newPackage):
		logger.info(u"Cleaning up in %s" % self.config["packageDir"])

		try:
			setRights(self.config["packageDir"])
		except Exception as error:
			logger.warning(u"Failed to set rights on directory '{0}': {1}".format(self.config["packageDir"], error))

		for filename in os.listdir(self.config["packageDir"]):
			path = os.path.join(self.config["packageDir"], filename)
			if not os.path.isfile(path):
				continue
			if path.endswith('.zs-old'):
				os.unlink(path)
				continue

			try:
				productId, version = parseFilename(filename)
			except Exception as error:
				logger.debug("Parsing {0!r} failed: {1!r}", filename, error)
				continue

			if productId == newPackage["productId"] and version != newPackage["version"]:
				logger.info(u"Deleting obsolete package file '%s'" % path)
				os.unlink(path)

		packageFile = os.path.join(self.config["packageDir"], newPackage["filename"])

		md5sumFile = u'{package}.md5'.format(package=packageFile)
		logger.info(u"Creating md5sum file '%s'" % md5sumFile)

		with open(md5sumFile, 'w') as hashFile:
			hashFile.write(md5sum(packageFile))

		setRights(md5sumFile)

		zsyncFile = u'{package}.zsync'.format(package=packageFile)
		logger.info(u"Creating zsync file '%s'" % zsyncFile)
		try:
			zsyncFile = ZsyncFile(zsyncFile)
			zsyncFile.generate(packageFile)
		except Exception as error:
			logger.error(u"Failed to create zsync file '%s': %s" % (zsyncFile, error))

	def onlyNewestPackages(self, packages):
		newestPackages = []
		for package in packages:
			found = None
			for i, newPackage in enumerate(newestPackages):
				if newPackage['productId'] == package['productId']:
					found = i
					if compareVersions(package['version'], '>', newestPackages[i]['version']):
						logger.debug("Package version '%s' is newer than version '%s'" % (package['version'], newestPackages[i]['version']))
						newestPackages[i] = package
					break

			if found is None:
				newestPackages.append(package)

		return newestPackages

	def getLocalPackages(self):
		logger.info(u"Getting info for local packages in '%s'" % self.config["packageDir"])
		packages = []
		for filename in os.listdir(self.config["packageDir"]):
			if not filename.endswith('.opsi'):
				continue

			packageFile = os.path.join(self.config["packageDir"], filename)
			logger.info(u"Found local package '%s'" % packageFile)
			try:
				productId, version = parseFilename(filename)
				checkSumFile = filename + '.md5'
				if not self.config['forceChecksumCalculation'] and os.path.exists(checkSumFile):
					logger.debug("Reading existing checksum from {0}", checkSumFile)
					with open(checkSumFile) as hashFile:
						packageMd5 = hashFile.read().strip()
				else:
					logger.debug("Calculating checksum for {0}", packageFile)
					packageMd5 = md5sum(packageFile)

				packageInfo = {
					"productId": forceProductId(productId),
					"version": version,
					"packageFile": packageFile,
					"filename": filename,
					"md5sum": packageMd5
				}
				logger.debug(u"Local package info: %s" % packageInfo)
				packages.append(packageInfo)
			except Exception as exc:
				logger.error("Failed to process file '%s': %s" % (filename, exc))
		return packages

	def getInstalledProducts(self):
		logger.info(u"Getting installed products")
		products = []
		configBackend = self.getConfigBackend()
		for product in configBackend.productOnDepot_getHashes(depotId=self.depotId):
			logger.info(u"Found installed product '%s_%s-%s'" % (product['productId'], product['productVersion'], product['packageVersion']))
			products.append(product)
		return products

	def getDownloadablePackages(self):
		downloadablePackages = []
		for repository in self.getActiveRepositories():
			logger.info(u"Getting package infos from repository '%s'" % repository.name)
			for package in self.getDownloadablePackagesFromRepository(repository):
				downloadablePackages.append(package)
		return downloadablePackages

	def getDownloadablePackagesFromRepository(self, repository):
		depotConnection = None
		depotRepositoryPath = None
		if repository.opsiDepotId:
			depotConnection = self.getDepotConnection(repository.opsiDepotId, repository.username, repository.password)
			repositoryLocalUrl = depotConnection.getDepot_hash(repository.opsiDepotId).get("repositoryLocalUrl")
			logger.info(u"Got repository local url '%s' for depot '%s'" % (repositoryLocalUrl, repository.opsiDepotId))
			if not repositoryLocalUrl or not repositoryLocalUrl.startswith('file://'):
				raise ValueError(u"Invalid repository local url for depot '%s'" % repository.opsiDepotId)
			depotRepositoryPath = repositoryLocalUrl[7:]

		passwordManager = urllib2.HTTPPasswordMgrWithDefaultRealm()
		passwordManager.add_password(None, repository.baseUrl.encode('utf-8'), repository.username.encode('utf-8'), repository.password.encode('utf-8'))
		handler = urllib2.HTTPBasicAuthHandler(passwordManager)
		if repository.proxy:
			logger.notice(u"Using Proxy: %s" % repository.proxy)
			proxyHandler = urllib2.ProxyHandler(
				{
					'http': repository.proxy,
					'https': repository.proxy
				}
			)
			opener = urllib2.build_opener(proxyHandler, handler)
		else:
			opener = urllib2.build_opener(handler)
		urllib2.install_opener(opener)

		packages = []
		errors = set()

		for url in repository.getDownloadUrls():
			try:
				url = urllib.quote(url.encode('utf-8'), safe="/#%[]=:;$&()+,!?*@'~")
				req = urllib2.Request(url, None, self.httpHeaders)
				response = opener.open(req)
				content = response.read()
				logger.debug("content: '%s'" % content)
				format = formatter.NullFormatter()
				htmlParser = LinksExtractor(format)
				htmlParser.feed(content)
				htmlParser.close()
				for link in htmlParser.getLinks():
					if not link.endswith('.opsi'):
						continue

					excluded = False
					included = True

					if repository.includes:
						included = False
						for include in repository.includes:
							if include.search(link):
								included = True
								break
					if not included:
						logger.info(u"Package '%s' is not included. Please check your includeProductIds-entry in configurationfile." % link)
						continue

					for exclude in repository.excludes:
						if exclude.search(link):
							excluded = True
							break
					if excluded:
						logger.info(u"Package '%s' excluded by regular expression" % link)
						continue

					try:
						productId, version = parseFilename(link)
						packageFile = url + '/' + link
						logger.info(u"Found opsi package: %s" % packageFile)
						packageInfo = {
							"repository": repository,
							"productId": forceProductId(productId),
							"version": version,
							"packageFile": packageFile,
							"filename": link,
							"md5sum": None,
							"zsyncFile": None
						}
						if depotConnection:
							packageInfo["md5sum"] = depotConnection.getMD5Sum(u'%s/%s' % (depotRepositoryPath, link))
						logger.debug(u"Repository package info: {0}", packageInfo)
						packages.append(packageInfo)
					except Exception as error:
						logger.error(u"Failed to process link '%s': %s" % (link, error))

				if not depotConnection:
					for link in htmlParser.getLinks():
						isMd5 = link.endswith('.opsi.md5')
						isZsync = link.endswith('.opsi.zsync')

						filename = None
						if isMd5:
							filename = link[:-4]
						elif isZsync:
							filename = link[:-6]
						else:
							continue

						try:
							for i, package in enumerate(packages):
								if package.get('filename') == filename:
									if isMd5:
										req = urllib2.Request(url + '/' + link, None, self.httpHeaders)
										con = opener.open(req)
										md5sum = con.read(32768)
										match = re.search('([a-z\d]{32})', md5sum)
										if match:
											foundMd5sum = match.group(1)
											packages[i]["md5sum"] = foundMd5sum
											logger.debug(u"Got md5sum for package {0!r}: {1}", filename, foundMd5sum)
									elif isZsync:
										zsyncFile = url + '/' + link
										packages[i]["zsyncFile"] = zsyncFile
										logger.debug(u"Found zsync file for package {0!r}: {1}", filename, zsyncFile)
									break
						except Exception as error:
							logger.error(u"Failed to process link '%s': %s" % (link, error))
			except Exception as error:
				logger.logException(error, LOG_DEBUG)
				self.errors.append(error)
				errors.add(forceUnicode(error))

		if errors:
			logger.warning("Problems processing repository {name}: {errors}", name=repository.name, errors='; '.join(str(e) for e in errors))

		return packages
