#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
   = = = = = = = = = = = = = = = = = = = = = = =
   =   opsi configuration daemon (opsiconfd)   =
   = = = = = = = = = = = = = = = = = = = = = = =
   
   opsiconfd is part of the desktop management solution opsi
   (open pc server integration) http://www.opsi.org
   
   Copyright (C) 2010 uib GmbH
   
   http://www.uib.de/
   
   All rights reserved.
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License version 2 as
   published by the Free Software Foundation.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
   
   @copyright:	uib GmbH <info@uib.de>
   @author: Christian Kampka <c.kampka@uib.de>
   @license: GNU General Public License version 2
"""

import re, os, time, functools

from twisted.application.service import Service
from twisted.internet.protocol import Protocol
from twisted.internet import reactor
from twisted.internet.task import LoopingCall

from OPSI.Backend.BackendManager import BackendManager, backendManagerFactory
from OPSI.Service.Process import OpsiPyDaemon
from OPSI.Util.Configuration import BaseConfiguration
from OPSI.Util.amp import OpsiProcessProtocolFactory
from OPSI.Service.JsonRpc import JsonRpcRequestProcessor
from OPSI.Logger import *
logger = Logger()

STOP_SIGNAL = '__STOP_SIGNAL__'

class BackendProcessConfiguration(BaseConfiguration):
	
	def _makeParser(self):
		parser = BaseConfiguration._makeParser(self)
		
		parser.add_option("--socket", dest="socket")
		parser.add_option("--logFile", dest="logFile")
		return parser

class BackendDataExchangeProtocol(Protocol):
	
	def dataReceived(self, data):
		pass
	
	def write(self, data):
		self.transport.write(data)

class OpsiBackendService(Service):
	
	def __init__(self, config):
		self._config = config
		
		self._backendManager = None
		self._socket = None
		self._lastPingReceived = time.time()
		self._check = LoopingCall(self.checkConnected)
	
	def checkConnected(self):
		if ((time.time() - self._lastPingReceived) > 30):
			reactor.stop()
	
	def setLogging(self, console=LOG_WARNING, file=LOG_WARNING):
		logger.setConsoleLevel(console)
		logger.setFileLevel(file)
	
	def startService(self):
		logger.setLogFile(self._config.logFile)

		if not os.path.exists(os.path.dirname(self._config.socket)):
			os.makedirs(os.path.dirname(self._config.socket))
		
		logger.notice("Opening socket %s for interprocess communication." % self._config.socket)
		self._socket = reactor.listenUNIX(self._config.socket, OpsiProcessProtocolFactory(self))
		self._check.start(10)

	def initialize(self, user, password, dispatchConfigFile, backendConfigDir,
				extensionConfigDir, aclFile, depotId, postpath):
		
		self.user = user
		self.password = password
		
		self._backend = BackendManager(
			dispatchConfigFile = dispatchConfigFile,
			backendConfigDir   = backendConfigDir,
			extensionConfigDir = extensionConfigDir,
			depotBackend       = bool(depotId)
		)
		
		self._backendManager = backendManagerFactory(
			user               = user,
			password           = password,
			dispatchConfigFile = dispatchConfigFile,
			backendConfigDir   = backendConfigDir,
			extensionConfigDir = extensionConfigDir,
			aclFile            = aclFile,
			depotId            = depotId,
			postpath           = postpath,
			context            = self._backend
		)
	
	def stopService(self):
		self._check.stop()
		
		if self._backend:
			self._backend.backend_exit()
		if self._backendManager:
			self._backendManager.backend_exit()
		if self._socket is not None:
			d = self._socket.stopListening()
			d.addCallback(lambda x: self._cleanup)
			
	def _cleanup(self):
		if os.path.exists(self._socket):
			os.unlink(self._socket)
	
	def processRequest(self, request):
		decoder = JsonRpcRequestProcessor(request, self._backendManager)
		decoder.decodeQuery()
		decoder.buildRpcs()
		d = decoder.executeRpcs(False)
		d.addCallback(lambda x: decoder.getResults())
		return d
	
	def backend_exit(self):
		
		return STOP_SIGNAL
	
	def isRunning(self):
		self._lastPingReceived = time.time()
		return True
	
	def __getattr__(self, name):
		if self._backendManager is not None:
			return getattr(self._backendManager, name, None)
		
class OpsiBackendProcess(OpsiPyDaemon):
	
	user = "opsiconfd"
	serviceClass = OpsiBackendService
	configurationClass = BackendProcessConfiguration
	
	def __init__(self, socket, args=[], reactor=reactor, logFile = logger.getLogFile()):
		
		args.extend(["--socket", socket, "--logFile", logFile])
		OpsiPyDaemon.__init__(self, socket = socket, args = args, reactor = reactor)
		self.check = LoopingCall(self.checkRunning)
	
	def start(self):
		logger.info(u"Starting new backend worker process")
		OpsiPyDaemon.start(self)
		reactor.callLater(2, self.check.start, 10, True)
	
	def checkRunning(self):
		d = self.isRunning()
		d.addCallback(self.restart)

	def restart(self, isRunning):
		if not isRunning and self.allowRestart:
			d = self.stop()
			d.addCallback(lambda x: self.start)
	
	def processRequest(self, request):
		d = self.callRemote("processRequest", request)
		d.addCallback(self.maybeStopped)
		return d
	
	def maybeStopped(self, result):
		for jsonrpc in result:
			if (jsonrpc.method == 'backend_exit'):
				self.stop()
		return result
	
	def stop(self):
		logger.info(u"Stopping backend worker process (pid: %s)" % self._process.pid)
		if self.check.running:
			self.check.stop()
		OpsiPyDaemon.stop(self)
	
	def callRemote(self, method, *args, **kwargs):
		return OpsiPyDaemon.callRemote(self, method, *args, **kwargs)
		
	def __getattr__(self, name):
		return functools.partial(self.callRemote, name)





