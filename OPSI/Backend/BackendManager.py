# -*- coding: utf-8 -*-

# This file is part of python-opsi.
# Copyright (C) 2006-2019 uib GmbH <info@uib.de>

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
BackendManager.

If you want to work with an opsi backend in i.e. a script a
BackendManager instance should be your first choice.
A BackendManager instance does the heavy lifting for you so you don't
need to set up you backends, ACL, multiplexing etc. yourself.

:copyright: uib GmbH <info@uib.de>
:author: Jan Schneider <j.schneider@uib.de>
:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

import inspect
import os
import re
import socket
import sys
import types
from contextlib import closing

from OPSI.Backend.Backend import (
	Backend, ConfigDataBackend, ExtendedBackend, ExtendedConfigDataBackend,
	getArgAndCallString)
from OPSI.Backend.Depotserver import DepotserverBackend
from OPSI.Backend.HostControl import HostControlBackend
from OPSI.Backend.HostControlSafe import HostControlSafeBackend
from OPSI.Config import OPSI_ADMIN_GROUP
from OPSI.Exceptions import *  # this is needed for dynamic extension loading
from OPSI.Logger import Logger, LOG_INFO
from OPSI.Object import BaseObject, mandatoryConstructorArgs
from OPSI.Object import *  # this is needed for dynamic extension loading
from OPSI.Types import *  # this is needed for dynamic extension loading
from OPSI.Util import objectToBeautifiedText, getfqdn  # used in extensions
from OPSI.Util.File.Opsi import BackendACLFile, BackendDispatchConfigFile, OpsiConfFile

if os.name == 'posix':
	import grp
	import PAM
	import pwd
elif os.name == 'nt':
	import win32net
	import win32security

__all__ = (
	'BackendManager', 'BackendDispatcher', 'BackendExtender',
	'BackendAccessControl', 'backendManagerFactory'
)

logger = Logger()

try:
	from OPSI.System.Posix import Distribution
	DISTRIBUTOR = Distribution().distributor or 'unknown'
except ImportError:
	# Probably running on Windows.
	DISTRIBUTOR = 'unknown'

try:
	with closing(os.popen('lsb_release -d 2>&1 /dev/null')) as f:
		DISTRIBUTION = f.read().split(':')[1].strip()
except Exception as error:
	logger.debug("Reading Distribution failed: {0}".format(error))
	DISTRIBUTION = 'unknown'


def loadBackendConfig(path):
	"""
	Load the backend configuration at `path`.

	:param path: Path to the configuration file to load.
	:type path: str
	:rtype: dict
	"""
	if not os.path.exists(path):
		raise BackendConfigurationError(u"Backend config file '%s' not found" % path)

	moduleGlobals = {
		'config': {},  # Will be filled after loading
		'module': '',  # Will be filled after loading
		'os': os,
		'socket': socket,
		'sys': sys,
	}
	execfile(path, moduleGlobals)
	return moduleGlobals


class BackendManager(ExtendedBackend):
	"""
	The BackendManager manages the backend and glues together various parts.

	This includes extending the backends, dispatching calls to backends,
	limiting the access	through ACL.
	"""

	def __init__(self, **kwargs):
		"""
		Creating a BackendManager.

		If no configuration is given a default config for accessing the
		local backend as configured through the files in
		/etc/opsi/backendManager/ will be used.

		The constructor takes several parameters and these are all optional.

		:param username: A username for authentication.
		:type username: str
		:param password: The corresponding password.
		:type password: str
		:param backend: A backend to use.
		:param dispatchconfig: A pre-definded dispatch config to use.
		:param dispatchconfigfile: The configuration file for dispatching.
		:type dispatchconfigfile: str
		:param backendconfigdir: The location of backend configurations.
		:type backendconfigdir: str
		:param depotbackend: Allow depot actions?
		:type depotbackend: bool
		:param hostcontrolbackend: Allow controlling hosts?
		:type hostcontrolbackend: bool
		:param hostcontrolsafebackend: Allow controlling hosts (safe variant)?
		:type hostcontrolsafebackend: bool
		:param extensionconfigdir: The directory where backend extensions can be found.
		:type extensionconfigdir: str
		:param extend: Extend the backends?
		:type extend: bool
		:param acl: An access control list (ACL) configuration to use.
		:type acl: [[str, ]]
		:param aclfile: Load the ACL from this file.
		:type aclfile: str
		"""
		self._backend = None
		self._backendConfigDir = None
		self._options = {}
		self._overwrite = True
		self._context = self

		Backend.__init__(self, **kwargs)

		username = None
		password = None
		dispatch = False
		extend = False
		extensionConfigDir = None
		extensionClass = None
		accessControl = False
		depotBackend = False
		hostControlBackend = False
		hostControlSafeBackend = False
		startReactor = True
		loadBackend = None

		if not kwargs:
			kwargs = {
				"dispatchConfigFile": u'/etc/opsi/backendManager/dispatch.conf',
				"backendConfigDir": u'/etc/opsi/backends',
				"extensionConfigDir": u'/etc/opsi/backendManager/extend.d',
				"depotBackend": True,
				"hostControlBackend": True,
				"hostControlSafeBackend": True,
			}
			logger.debug("No config given, using {0!r}".format(kwargs))

		for (option, value) in kwargs.items():
			option = option.lower()
			if option == 'username':
				username = value
			elif option == 'password':
				password = value
			elif option == 'backend':
				if isinstance(value, (str, unicode)):
					loadBackend = value
				else:
					self._backend = value
				del kwargs[option]
			elif option == 'backendconfigdir':
				self._backendConfigDir = value
			elif option in ('dispatchconfig', 'dispatchconfigfile') and value:
				dispatch = True
			elif option == 'depotbackend':
				depotBackend = forceBool(value)
			elif option == 'hostcontrolbackend':
				hostControlBackend = forceBool(value)
			elif option == 'hostcontrolsafebackend':
				hostControlSafeBackend = forceBool(value)
			elif option == 'extensionconfigdir' and value:
				extensionConfigDir = value
				extend = True
			elif option == 'extensionclass':
				extensionClass = value
				extend = True
			elif option == 'extend':
				extend = forceBool(value)
			elif option in ('acl', 'aclfile') and value:
				accessControl = True
			elif option == 'startreactor' and value is False:
				startReactor = False

		if loadBackend:
			logger.info(u"* BackendManager is loading backend '%s'" % loadBackend)
			self._backend = self.__loadBackend(loadBackend)
			# self._backend is now a ConfigDataBackend

		if not dispatch and not self._backend:
			raise BackendConfigurationError(u"Neither backend nor dispatch config given")

		if dispatch:
			logger.info(u"* BackendManager is creating BackendDispatcher")
			self._backend = BackendDispatcher(context=self, **kwargs)
			# self._backend is now a BackendDispatcher which is a ConfigDataBackend

		if extend or depotBackend:
			logger.info(u"* BackendManager is creating ExtendedConfigDataBackend")
			# DepotserverBackend/BackendExtender need ExtendedConfigDataBackend backend
			self._backend = ExtendedConfigDataBackend(self._backend)
			# self._backend is now an ExtendedConfigDataBackend

		if depotBackend:
			logger.info(u"* BackendManager is creating DepotserverBackend")
			self._backend = DepotserverBackend(self._backend)

		if hostControlBackend:
			logger.info(u"* BackendManager is creating HostControlBackend")
			try:
				hostControlBackendConfig = self.__loadBackendConfig('hostcontrol')['config']
			except Exception as backendConfigLoadError:
				logger.error(
					"Failed to load configuration for HostControlBackend: {}",
					backendConfigLoadError
				)
				hostControlBackendConfig = {}
			self._backend = HostControlBackend(self._backend, **hostControlBackendConfig)

		if hostControlSafeBackend:
			logger.info(u"* BackendManager is creating HostControlSafeBackend")
			try:
				hostControlSafeBackendConfig = self.__loadBackendConfig('hostcontrol')['config']
			except Exception as backendConfigLoadError:
				logger.error(
					"Failed to load configuration for HostControlSafeBackend: {}",
					backendConfigLoadError
				)
				hostControlSafeBackendConfig = {}
			self._backend = HostControlSafeBackend(self._backend, **hostControlSafeBackendConfig)

		if accessControl:
			logger.info(u"* BackendManager is creating BackendAccessControl")
			self._backend = BackendAccessControl(backend=self._backend, **kwargs)

		if extensionConfigDir or extensionClass:
			logger.info(u"* BackendManager is creating BackendExtender")
			self._backend = BackendExtender(self._backend, **kwargs)

		self._createInstanceMethods()

	def __loadBackendConfig(self, name):
		if not self._backendConfigDir:
			raise BackendConfigurationError(u"Backend config dir not given")
		if not os.path.exists(self._backendConfigDir):
			raise BackendConfigurationError(u"Backend config dir '%s' not found" % self._backendConfigDir)
		if not re.search(r'^[a-zA-Z0-9-_]+$', name):
			raise ValueError(u"Bad backend config name '%s'" % name)
		name = name.lower()
		backendConfigFile = os.path.join(self._backendConfigDir, '%s.conf' % name)
		return loadBackendConfig(backendConfigFile)

	def __loadBackend(self, name):
		config = self.__loadBackendConfig(name)
		backendConfigFile = os.path.join(self._backendConfigDir, '%s.conf' % name)
		if not config['module']:
			raise BackendConfigurationError(u"No module defined in backend config file '%s'" % backendConfigFile)
		if not isinstance(config['config'], dict):
			raise BackendConfigurationError(u"Bad type for config var in backend config file '%s', has to be dict" % backendConfigFile)
		config['config']['name'] = name
		exec(u'from %s import %sBackend' % (config['module'], config['module']))
		return eval(u'%sBackend(**config["config"])' % config['module'])


class BackendDispatcher(Backend):
	def __init__(self, **kwargs):
		Backend.__init__(self, **kwargs)

		self._dispatchConfigFile = None
		self._dispatchConfig = []
		self._dispatchIgnoreModules = []
		self._backendConfigDir = None
		self._backends = {}
		self._options = {}
		self._context = self

		for (option, value) in kwargs.items():
			option = option.lower()
			if option == 'dispatchconfig':
				self._dispatchConfig = value
			elif option == 'dispatchconfigfile':
				self._dispatchConfigFile = value
			elif option == 'dispatchignoremodules' and value:
				self._dispatchIgnoreModules = forceList(value)
			elif option == 'backendconfigdir':
				self._backendConfigDir = value
			elif option == 'context':
				self._context = value

		if self._dispatchConfigFile:
			logger.info(u"Loading dispatch config file '%s'" % self._dispatchConfigFile)
			self.__loadDispatchConfig()

		if not self._dispatchConfig:
			raise BackendConfigurationError(u"Dispatcher not configured")

		self.__loadBackends()
		self._createInstanceMethods()

	def __repr__(self):
		additionalInformation = []
		try:
			if self._dispatchIgnoreModules:
				additionalInformation.append('dispatchIgnoreModules={0!r}'.format(self._dispatchIgnoreModules))

			if self._dispatchConfigFile:
				additionalInformation.append('dispatchConfigFile={0!r}'.format(self._dispatchConfigFile))
			elif self._dispatchConfig:
				additionalInformation.append('dispatchConfig={0!r}'.format(self._dispatchConfig))

			if self._context != self:
				additionalInformation.append('context={0!r}'.format(self._context))
		except AttributeError:
			# Can happen during initialisation
			pass

		return '<{0}({1})>'.format(self.__class__.__name__, ', '.join(additionalInformation))

	def __loadDispatchConfig(self):
		if not self._dispatchConfigFile:
			raise BackendConfigurationError(u"No dispatch config file defined")

		if not os.path.exists(self._dispatchConfigFile):
			raise BackendConfigurationError(u"Dispatch config file '%s' not found" % self._dispatchConfigFile)

		try:
			self._dispatchConfig = BackendDispatchConfigFile(self._dispatchConfigFile).parse()
			logger.debug(u"Read dispatch config from file {0!r}: {1!r}", self._dispatchConfigFile, self._dispatchConfig)
		except Exception as e:
			raise BackendConfigurationError(u"Failed to load dispatch config file '%s': %s" % (self._dispatchConfigFile, e))

	def __loadBackends(self):
		if not self._backendConfigDir:
			raise BackendConfigurationError(u"Backend config dir not given")

		if not os.path.exists(self._backendConfigDir):
			raise BackendConfigurationError(u"Backend config dir '%s' not found" % self._backendConfigDir)

		collectedBackends = set()
		for pattern, backends in self._dispatchConfig:
			for backend in backends:
				if not backend:
					raise BackendConfigurationError(u"Bad dispatcher config: {0!r} has empty target backend: {1!r}".format(pattern, backends))

				collectedBackends.add(backend)

		for backend in collectedBackends:
			self._backends[backend] = {}
			backendConfigFile = os.path.join(self._backendConfigDir, '%s.conf' % backend)
			logger.info(u"Loading backend config '%s'" % backendConfigFile)
			l = loadBackendConfig(backendConfigFile)
			if not l['module']:
				raise BackendConfigurationError(u"No module defined in backend config file '%s'" % backendConfigFile)
			if l['module'] in self._dispatchIgnoreModules:
				logger.notice(u"Ignoring module '%s', backend '%s'" % (l['module'], backend))
				del self._backends[backend]
				continue
			if not isinstance(l['config'], dict):
				raise BackendConfigurationError(u"Bad type for config var in backend config file '%s', has to be dict" % backendConfigFile)
			backendInstance = None
			l["config"]["context"] = self
			b = __import__(l['module'], globals(), locals(), "%sBackend" % l['module'], -1)
			self._backends[backend]["instance"] = getattr(b, "%sBackend" % l['module'])(**l['config'])

	def _createInstanceMethods(self):
		logger.debug(u"BackendDispatcher is creating instance methods")
		for Class in (ConfigDataBackend, ):  # Also apply to ExtendedConfigDataBackend?
			for methodName, functionRef in inspect.getmembers(Class, inspect.ismethod):
				if methodName.startswith('_'):
					# Not a public method
					continue
				logger.debug2(u"Found public %s method '%s'" % (Class.__name__, methodName))

				if hasattr(self, methodName):
					logger.debug(u"{0}: skipping already present method {1}", self.__class__.__name__, methodName)
					continue

				methodBackends = []
				for regex, backends in self._dispatchConfig:
					if not re.search(regex, methodName):
						continue

					for backend in forceList(backends):
						if backend not in self._backends:
							logger.debug(u"Ignoring backend {0!r}: backend not available", backend)
							continue
						methodBackends.append(backend)
					logger.debug(u"{0!r} matches method {1!r}, dispatching to backends: {2}", regex, methodName, u', '.join(methodBackends))
					break

				if not methodBackends:
					continue

				argString, callString = getArgAndCallString(functionRef)

				exec(u'def %s(self, %s): return self._dispatchMethod(%s, "%s", %s)' % (methodName, argString, methodBackends, methodName, callString))
				setattr(self, methodName, types.MethodType(eval(methodName), self))

	def _dispatchMethod(self, methodBackends, methodName, **kwargs):
		logger.debug(u"Dispatching method {0!r} to backends: {1}", methodName, methodBackends)
		result = None

		for methodBackend in methodBackends:
			meth = getattr(self._backends[methodBackend]["instance"], methodName)
			res = meth(**kwargs)

			# TODO: handling of generators?
			if isinstance(result, list) and isinstance(res, list):
				result.extend(res)
			elif isinstance(result, dict) and isinstance(res, dict):
				result.update(res)
			elif isinstance(result, set) and isinstance(res, set):
				result = result.union(res)
			elif isinstance(result, tuple) and isinstance(res, tuple):
				result = result + res
			elif res is not None:
				result = res

		logger.debug2(u"Finished dispatching method {0!r}", methodName)
		return result

	def backend_setOptions(self, options):
		Backend.backend_setOptions(self, options)
		for be in self._backends.values():
			be['instance'].backend_setOptions(options)

	def backend_getOptions(self):
		options = Backend.backend_getOptions(self)
		for be in self._backends.values():
			options.update(be['instance'].backend_getOptions())
		return options

	def backend_exit(self):
		for be in self._backends.values():
			be['instance'].backend_exit()

	def dispatcher_getConfig(self):
		return self._dispatchConfig

	def dispatcher_getBackendNames(self):
		return self._backends.keys()


class BackendExtender(ExtendedBackend):
	def __init__(self, backend, **kwargs):
		if not isinstance(backend, ExtendedBackend) and not isinstance(backend, BackendDispatcher):
			if not isinstance(backend, BackendAccessControl) or (not isinstance(backend._backend, ExtendedBackend) and not isinstance(backend._backend, BackendDispatcher)):
				raise TypeError("BackendExtender needs instance of ExtendedBackend or BackendDispatcher as backend, got %s" % backend.__class__.__name__)

		ExtendedBackend.__init__(self, backend, overwrite=kwargs.get('overwrite', True))

		self._extensionConfigDir = None
		self._extensionClass = None

		for (option, value) in kwargs.items():
			option = option.lower()
			if option == 'extensionconfigdir':
				self._extensionConfigDir = value
			elif option == 'extensionclass':
				self._extensionClass = value

		self.__createExtensions()

	def __createExtensions(self):
		if self._extensionClass:
			for methodName, functionRef in inspect.getmembers(self._extensionClass, inspect.ismethod):
				if methodName.startswith('_'):
					continue
				logger.debug2(u"Extending {0} with instancemethod: {1!r}", self._backend.__class__.__name__, methodName)
				new_function = types.FunctionType(functionRef.func_code, functionRef.func_globals, functionRef.func_code.co_name)
				new_method = types.MethodType(new_function, self)
				setattr(self, methodName, new_method)

		if self._extensionConfigDir:
			if not os.path.exists(self._extensionConfigDir):
				logger.error(u"No extensions loaded: extension directory {0!r} does not exist".format(self._extensionConfigDir))
				return

			try:
				confFiles = (
					os.path.join(self._extensionConfigDir, filename)
					for filename in sorted(os.listdir(self._extensionConfigDir))
					if filename.endswith('.conf')
				)

				for confFile in confFiles:
					try:
						logger.info(u"Reading config file '%s'" % confFile)
						execfile(confFile)
					except Exception as execError:
						logger.logException(execError)
						raise RuntimeError(u"Error reading file {0!r}: {1}".format(confFile, execError))

					for key, val in locals().items():
						if isinstance(val, types.FunctionType):   # TODO: find a better way
							logger.debug2(u"Extending %s with instancemethod: '%s'" % (self._backend.__class__.__name__, key))
							setattr(self, key, types.MethodType(val, self))
			except Exception as error:
				raise BackendConfigurationError(u"Failed to read extensions from '%s': %s" % (self._extensionConfigDir, error))


class BackendAccessControl(object):

	def __init__(self, backend, **kwargs):

		self._backend = backend
		self._context = backend
		self._username = None
		self._password = None
		self._acl = None
		self._aclFile = None
		self._pamService = 'common-auth'
		self._userGroups = set()
		self._forceGroups = None
		self._host = None
		self._authenticated = False

		if os.path.exists("/etc/pam.d/opsi-auth"):
			# Prefering our own - if present.
			self._pamService = 'opsi-auth'
		elif 'suse' in DISTRIBUTOR.lower():
			self._pamService = 'sshd'
		elif 'centos' in DISTRIBUTOR.lower() or 'redhat' in DISTRIBUTOR.lower():
			self._pamService = 'system-auth'

		for (option, value) in kwargs.items():
			option = option.lower()
			if option == 'username':
				self._username = value
			elif option == 'password':
				self._password = value
			elif option == 'acl':
				self._acl = value
			elif option == 'aclfile':
				self._aclFile = value
			elif option == 'pamservice':
				self._pamService = value
			elif option in ('context', 'accesscontrolcontext'):
				self._context = value
			elif option == 'forcegroups':
				if value is not None:
					self._forceGroups = forceUnicodeList(value)

		if not self._username:
			raise BackendAuthenticationError(u"No username specified")
		if not self._password:
			raise BackendAuthenticationError(u"No password specified")
		if not self._backend:
			raise BackendAuthenticationError(u"No backend specified")
		if isinstance(self._backend, BackendAccessControl):
			raise BackendConfigurationError(u"Cannot use BackendAccessControl instance as backend")

		try:
			if re.search(r'^[^.]+\.[^.]+\.\S+$', self._username):
				# Username starts with something like hostname.domain.tld:
				# Assuming it is a host passing his FQDN as username
				logger.debug(u"Trying to authenticate by opsiHostKey...")
				self._username = self._username.lower()

				try:
					host = self._context.host_getObjects(id=self._username)
				except AttributeError as aerr:
					logger.debug(u"{0!r}", aerr)
					raise BackendUnaccomplishableError(u"Passed backend has no method 'host_getObjects', cannot authenticate host '%s'" % self._username)

				try:
					self._host = host[0]
				except IndexError as ierr:
					logger.debug(u"{0!r}", ierr)
					raise BackendMissingDataError(u"Host '%s' not found in backend %s" % (self._username, self._context))

				if not self._host.opsiHostKey:
					raise BackendMissingDataError(u"OpsiHostKey not found for host '%s'" % self._username)

				logger.confidential(u"Client {0!r}, key sent {1!r}, key stored {2!r}", self._username, self._password, self._host.opsiHostKey)

				if self._password != self._host.opsiHostKey:
					raise BackendAuthenticationError(u"OpsiHostKey authentication failed for host '%s': wrong key" % self._host.id)

				logger.info(u"OpsiHostKey authentication successful for host {0!r}", self._host.id)
			else:
				# System user trying to log in with username and password
				logger.debug(u"Trying to authenticate by operating system...")
				self._authenticateUser()
				# Authentication did not throw exception => authentication successful
				logger.info(u"Operating system authentication successful for user '%s', groups '%s'" % (self._username, ','.join(self._userGroups)))
		except Exception as e:
			raise BackendAuthenticationError(forceUnicode(e))

		self._createInstanceMethods()
		if self._aclFile:
			self.__loadACLFile()
		self._authenticated = True

		if not self._acl:
			self._acl = [[r'.*', [{'type': u'sys_group', 'ids': [OPSI_ADMIN_GROUP], 'denyAttributes': [], 'allowAttributes': []}]]]

		# Pre-compiling regex patterns for speedup.
		for i, (pattern, acl) in enumerate(self._acl):
			self._acl[i] = (re.compile(pattern), acl)

	def accessControl_authenticated(self):
		return self._authenticated

	def accessControl_userIsAdmin(self):
		return self._isMemberOfGroup(OPSI_ADMIN_GROUP) or self._isOpsiDepotserver()

	def accessControl_userIsReadOnlyUser(self):
		readOnlyGroups = OpsiConfFile().getOpsiGroups('readonly')
		if readOnlyGroups:
			return self._isMemberOfGroup(readOnlyGroups)
		return False

	def __loadACLFile(self):
		try:
			if not self._aclFile:
				raise BackendConfigurationError(u"No acl file defined")
			if not os.path.exists(self._aclFile):
				raise BackendIOError(u"Acl file '%s' not found" % self._aclFile)
			self._acl = BackendACLFile(self._aclFile).parse()
			logger.debug(u"Read acl from file {0!r}: {1!r}", self._aclFile, self._acl)
		except Exception as error:
			logger.logException(error)
			raise BackendConfigurationError(u"Failed to load acl file '%s': %s" % (self._aclFile, error))

	def _createInstanceMethods(self):
		protectedMethods = set()
		for Class in (ExtendedConfigDataBackend, ConfigDataBackend, DepotserverBackend, HostControlBackend, HostControlSafeBackend):
			methodnames = (name for name, _ in inspect.getmembers(Class, inspect.ismethod) if not name.startswith('_'))
			for methodName in methodnames:
				protectedMethods.add(methodName)

		for methodName, functionRef in inspect.getmembers(self._backend, inspect.ismethod):
			if methodName.startswith('_'):
				# Not a public method
				continue

			argString, callString = getArgAndCallString(functionRef)

			if methodName in protectedMethods:
				logger.debug2(u"Protecting %s method '%s'" % (Class.__name__, methodName))
				exec(u'def %s(self, %s): return self._executeMethodProtected("%s", %s)' % (methodName, argString, methodName, callString))
			else:
				logger.debug2(u"Not protecting %s method '%s'" % (Class.__name__, methodName))
				exec(u'def %s(self, %s): return self._executeMethod("%s", %s)' % (methodName, argString, methodName, callString))

			setattr(self, methodName, types.MethodType(eval(methodName), self))

	def _authenticateUser(self):
		'''
		Authenticate a user by the underlying operating system.

		:raises BackendAuthenticationError: If authentication fails.
		'''
		if os.name == 'posix':
			self._pamAuthenticateUser()
		elif os.name == 'nt':
			self._winAuthenticateUser()
		else:
			raise NotImplementedError("Sorry, operating system '%s' not supported yet!" % os.name)

	def _winAuthenticateUser(self):
		'''
		Authenticate a user by Windows-Login on current machine

		:raises BackendAuthenticationError: If authentication fails.
		'''
		logger.confidential(u"Trying to authenticate user '%s' with password '%s' by win32security" % (self._username, self._password))

		try:
			win32security.LogonUser(self._username, 'None', self._password, win32security.LOGON32_LOGON_NETWORK, win32security.LOGON32_PROVIDER_DEFAULT)
			if self._forceGroups is not None:
				self._userGroups = set(self._forceGroups)
				logger.info(u"Forced groups for user '%s': %s" % (self._username, self._userGroups))
			else:
				gresume = 0
				while True:
					(groups, total, gresume) = win32net.NetLocalGroupEnum(None, 0, gresume)
					for groupname in (u['name'] for u in groups):
						logger.debug2(u"Found group '%s'" % groupname)
						uresume = 0
						while True:
							(users, total, uresume) = win32net.NetLocalGroupGetMembers(None, groupname, 0, uresume)
							for sid in (u['sid'] for u in users):
								(username, domain, type) = win32security.LookupAccountSid(None, sid)
								if username.lower() == self._username.lower():
									self._userGroups.add(groupname)
									logger.debug(u"User {0!r} is member of group {1!r}", self._username, groupname)
							if uresume == 0:
								break
						if gresume == 0:
							break
		except Exception as e:
			raise BackendAuthenticationError(u"Win32security authentication failed for user '%s': %s" % (self._username, e))

	def _pamAuthenticateUser(self):
		'''
		Authenticate a user by PAM (Pluggable Authentication Modules).
		Important: the uid running this code needs access to /etc/shadow
		if os uses traditional unix authentication mechanisms.

		:raises BackendAuthenticationError: If authentication fails.
		'''
		logger.confidential(u"Trying to authenticate user {0!r} with password {1!r} by PAM", self._username, self._password)

		class AuthConv:
			''' Handle PAM conversation '''
			def __init__(self, user, password):
				self.user = user
				self.password = password

			def __call__(self, auth, query_list, userData=None):
				response = []
				for (query, qtype) in query_list:
					logger.debug(u"PAM conversation: query {0!r}, type {1!r}", query, qtype)
					if qtype == PAM.PAM_PROMPT_ECHO_ON:
						response.append((self.user, 0))
					elif qtype == PAM.PAM_PROMPT_ECHO_OFF:
						response.append((self.password, 0))
					elif qtype in (PAM.PAM_ERROR_MSG, PAM.PAM_TEXT_INFO):
						response.append(('', 0))
					else:
						return None

				return response

		logger.debug2(u"Attempting PAM authentication as user {0!r}...", self._username)
		try:
			# Create instance
			auth = PAM.pam()
			auth.start(self._pamService)
			# Authenticate
			auth.set_item(PAM.PAM_CONV, AuthConv(self._username, self._password))
			# Set the tty
			# Workaround for:
			#   If running as daemon without a tty the following error
			#   occurs with older versions of pam:
			#      pam_access: couldn't get the tty name
			try:
				auth.set_item(PAM.PAM_TTY, '/dev/null')
			except Exception:
				pass
			auth.authenticate()
			auth.acct_mgmt()
			logger.debug2("PAM authentication successful.")

			if self._forceGroups is not None:
				self._userGroups = set(self._forceGroups)
				logger.info(u"Forced groups for user '%s': %s" % (self._username, self._userGroups))
			else:
				logger.debug("Reading groups of user...")
				primaryGroup = forceUnicode(grp.getgrgid(pwd.getpwnam(self._username)[3])[0])
				logger.debug(u"Primary group of user {0!r} is {1!r}", self._username, primaryGroup)

				self._userGroups = set(forceUnicode(group[0]) for group in grp.getgrall() if self._username in group[3])
				self._userGroups.add(primaryGroup)
				logger.debug(u"User {0!r} is member of groups: {1}", self._username, self._userGroups)
		except Exception as e:
			raise BackendAuthenticationError(u"PAM authentication failed for user '%s': %s" % (self._username, e))

	def _isMemberOfGroup(self, ids):
		for groupId in forceUnicodeList(ids):
			if groupId in self._userGroups:
				return True
		return False

	def _isUser(self, ids):
		return forceBool(self._username in forceUnicodeList(ids))

	def _isOpsiDepotserver(self, ids=[]):
		if not self._host or not isinstance(self._host, OpsiDepotserver):
			return False
		if not ids:
			return True

		for hostId in forceUnicodeList(ids):
			if hostId == self._host.id:
				return True
		return False

	def _isOpsiClient(self, ids=[]):
		if not self._host or not isinstance(self._host, OpsiClient):
			return False

		if not ids:
			return True

		return forceBool(self._host.id in forceUnicodeList(ids))

	def _isSelf(self, **params):
		if not params:
			return False
		for (param, value) in params.items():
			if isinstance(value, types.ClassType) and issubclass(value, Object) and (value.id == self._username):
				return True
			if param in ('id', 'objectId', 'hostId', 'clientId', 'serverId', 'depotId') and (value == self._username):
				return True
		return False

	def _executeMethod(self, methodName, **kwargs):
		meth = getattr(self._backend, methodName)
		return meth(**kwargs)

	def _executeMethodProtected(self, methodName, **kwargs):
		granted = False
		newKwargs = {}
		acls = []
		logger.debug(u"Access control for method {0!r} with params {1!r}", methodName, kwargs)
		for regex, acl in self._acl:
			logger.debug2(u"Testing if ACL pattern {0!r} matches method {1!r}", regex.pattern, methodName)
			if not regex.search(methodName):
				logger.debug2(u"No match -> skipping.")
				continue

			logger.debug(u"Found matching acl for method {1!r}: {0}", acl, methodName)
			for entry in acl:
				aclType = entry.get('type')
				ids = entry.get('ids', [])
				newGranted = False
				if aclType == 'all':
					newGranted = True
				elif aclType == 'opsi_depotserver':
					newGranted = self._isOpsiDepotserver(ids)
				elif aclType == 'opsi_client':
					newGranted = self._isOpsiClient(ids)
				elif aclType == 'sys_group':
					newGranted = self._isMemberOfGroup(ids)
				elif aclType == 'sys_user':
					newGranted = self._isUser(ids)
				elif aclType == 'self':
					newGranted = 'partial_object'
				else:
					logger.error(u"Unhandled acl entry type: {0}", aclType)
					continue

				if newGranted is False:
					continue

				if entry.get('denyAttributes') or entry.get('allowAttributes'):
					newGranted = 'partial_attributes'

				if newGranted:
					acls.append(entry)
					granted = newGranted

				if granted is True:
					break
			break

		logger.debug("Method {0!r} using acls: {1}", methodName, acls)
		if granted is True:
			logger.debug(u"Full access to method {0!r} granted to user {1!r} by acl {2!r}", methodName, self._username, acls[0])
			newKwargs = kwargs
		elif granted is False:
			raise BackendPermissionDeniedError(u"Access to method '%s' denied for user '%s'" % (methodName, self._username))
		else:
			logger.debug(u"Partial access to method {0!r} granted to user {1!r} by acls {2!r}", methodName, self._username, acls)
			try:
				newKwargs = self._filterParams(kwargs, acls)
				if not newKwargs:
					raise BackendPermissionDeniedError(u"No allowed param supplied")
			except Exception as e:
				logger.logException(e, LOG_INFO)
				raise BackendPermissionDeniedError(u"Access to method '%s' denied for user '%s': %s" % (methodName, self._username, e))

		logger.debug2("newKwargs: {0}", newKwargs)

		meth = getattr(self._backend, methodName)
		result = meth(**newKwargs)

		if granted is True:
			return result

		return self._filterResult(result, acls)

	def _filterParams(self, params, acls):
		logger.debug(u"Filtering params: {0}", params)
		for (key, value) in params.items():
			valueList = forceList(value)
			if not valueList:
				continue

			if issubclass(valueList[0].__class__, BaseObject) or isinstance(valueList[0], dict):
				valueList = self._filterObjects(valueList, acls, exceptionOnTruncate=False)
				if isinstance(value, list):
					params[key] = valueList
				else:
					if valueList:
						params[key] = valueList[0]
					else:
						del params[key]
		return params

	def _filterResult(self, result, acls):
		if result:
			resultList = forceList(result)
			if issubclass(resultList[0].__class__, BaseObject) or isinstance(resultList[0], dict):
				resultList = self._filterObjects(result, acls, exceptionOnTruncate=False, exceptionIfAllRemoved=False)
				if isinstance(result, list):
					return resultList
				else:
					if resultList:
						return resultList[0]
					else:
						return None
		return result

	def _filterObjects(self, objects, acls, exceptionOnTruncate=True, exceptionIfAllRemoved=True):
		logger.info(u"Filtering objects by acls")
		newObjects = []
		for obj in forceList(objects):
			isDict = isinstance(obj, dict)
			if isDict:
				objHash = obj
			else:
				objHash = obj.toHash()

			allowedAttributes = set()
			for acl in acls:
				if acl.get('type') == 'self':
					objectId = None
					for identifier in ('id', 'objectId', 'hostId', 'clientId', 'depotId', 'serverId'):
						try:
							objectId = objHash[identifier]
							break
						except KeyError:
							pass

					if not objectId or objectId != self._username:
						continue

				if acl.get('allowAttributes'):
					attributesToAdd = acl['allowAttributes']
				elif acl.get('denyAttributes'):
					attributesToAdd = (
						attribute for attribute in objHash
						if attribute not in acl['denyAttributes']
					)
				else:
					attributesToAdd = objHash.keys()

				for attribute in attributesToAdd:
					allowedAttributes.add(attribute)

			if not allowedAttributes:
				continue

			if not isDict:
				allowedAttributes.add('type')

				for attribute in mandatoryConstructorArgs(obj.__class__):
					allowedAttributes.add(attribute)

			for key in objHash.keys():
				if key not in allowedAttributes:
					if exceptionOnTruncate:
						raise BackendPermissionDeniedError(u"Access to attribute '%s' denied" % key)
					del objHash[key]

			if isDict:
				newObjects.append(objHash)
			else:
				newObjects.append(obj.__class__.fromHash(objHash))

		orilen = len(objects)
		newlen = len(newObjects)
		if newlen < orilen:
			logger.warning(u"{0} objects removed by acl, {1} objects left".format((orilen - newlen), newlen))
			if newlen == 0 and exceptionIfAllRemoved:
				raise BackendPermissionDeniedError(u"Access denied")

		return newObjects


def backendManagerFactory(user, password, dispatchConfigFile, backendConfigDir,
				extensionConfigDir, aclFile, depotId, postpath, context, **kwargs):
	backendManager = None
	if len(postpath) == 2 and postpath[0] == 'backend':
		backendManager = BackendManager(
			backend=postpath[1],
			accessControlContext=context,
			backendConfigDir=backendConfigDir,
			aclFile=aclFile,
			username=user,
			password=password,
			**kwargs
		)
	elif len(postpath) == 2 and postpath[0] == 'extend':
		extendPath = postpath[1]
		if not re.search(r'^[a-zA-Z0-9_-]+$', extendPath):
			raise ValueError(u"Extension config path '%s' refused" % extendPath)
		backendManager = BackendManager(
			dispatchConfigFile=dispatchConfigFile,
			backendConfigDir=backendConfigDir,
			extensionConfigDir=os.path.join(extensionConfigDir, extendPath),
			aclFile=aclFile,
			accessControlContext=context,
			depotBackend=bool(depotId),
			hostControlBackend=True,
			hostControlSafeBackend=True,
			username=user,
			password=password,
			**kwargs
		)
	else:
		backendManager = BackendManager(
			dispatchConfigFile=dispatchConfigFile,
			backendConfigDir=backendConfigDir,
			extensionConfigDir=extensionConfigDir,
			aclFile=aclFile,
			accessControlContext=context,
			depotBackend=bool(depotId),
			hostControlBackend=True,
			hostControlSafeBackend=True,
			username=user,
			password=password,
			**kwargs
		)

	return backendManager
