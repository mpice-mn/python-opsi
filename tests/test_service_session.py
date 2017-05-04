# -*- coding: utf-8 -*-

# This file is part of python-opsi.
# Copyright (C) 2015-2017 uib GmbH <info@uib.de>

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
Testing session and sessionhandler.

:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

import time

from OPSI.Service.Session import Session, SessionHandler
from OPSI.Types import OpsiAuthenticationError

import pytest


@pytest.fixture
def session():
	testSession = Session(FakeSessionHandler())
	try:
		yield testSession
	finally:
		# This may leave a thread running afterwards
		# if testSession and testSession.sessionTimer:
		try:
			testSession.sessionTimer.cancel()
			testSession.sessionTimer.join(1)
		except AttributeError:
			pass

		testSession.sessionTimer = None


@pytest.fixture
def sessionHandler():
	handler = SessionHandler()
	try:
		yield handler
	finally:
		handler.deleteAllSessions()


class FakeSessionHandler(object):
	def sessionExpired(self, session):
		pass


def testSessionUsageCount(session):
	assert 0 == session.usageCount

	session.increaseUsageCount()
	assert 1 == session.usageCount

	session.decreaseUsageCount()
	assert 0 == session.usageCount


def testUsageCountDoesNothingOnExpiredSession(session):
	assert 0 == session.usageCount

	session.delete()

	session.increaseUsageCount()
	assert 0 == session.usageCount

	session.decreaseUsageCount()
	assert 0 == session.usageCount


def testMarkingSessionForDeletion(session):
	assert not session.getMarkedForDeletion(), "New session should not be marked for deletion."

	session.setMarkedForDeletion()

	assert session.getMarkedForDeletion()


def testSessionValidity(session):
	assert session.getValidity()


def testDeletedSessionsAreMadeInvalid(session):
	session.delete()
	assert not session.getValidity()


def testSessionHandlerInitialisation():
	handler = SessionHandler("testapp", 10, maxSessionsPerIp=4, sessionDeletionTimeout=23)
	assert "testapp" == handler.sessionName
	assert 10 == handler.sessionMaxInactiveInterval
	assert 4 == handler.maxSessionsPerIp
	assert 23 == handler.sessionDeletionTimeout

	assert not handler.sessions


def testHandlerCreatesAndExpiresSessions():
	handler = SessionHandler(sessionDeletionTimeout=2)
	assert not handler.sessions

	session = handler.createSession()
	assert 1 == len(handler.sessions)
	assert handler == session.sessionHandler

	session.expire()
	assert 0 == len(handler.sessions)


def testDeletingAllSessions(sessionHandler):
	assert not sessionHandler.sessions

	for _ in range(10):
		sessionHandler.createSession()

	assert 10 == len(sessionHandler.sessions)

	sessionHandler.deleteAllSessions()
	assert 0 == len(sessionHandler.sessions)


def testSessionHandlerDeletingSessionInUse():
	handler = SessionHandler(sessionDeletionTimeout=2)
	assert not handler.sessions

	session = handler.createSession()
	assert 1 == len(handler.sessions)

	session.increaseUsageCount()
	session.increaseUsageCount()
	session.expire()

	assert 0 == len(handler.sessions)


def testDeletingNonExistingSessionMustNotFail(sessionHandler):
	sessionHandler.deleteSession('iAmNotHere')


@pytest.mark.parametrize("sessionCount", [256])
def testCreatingAndExpiringManySessions(sessionCount):
	"Creating a lot of sessions and wait for them to expire."

	deletion_time_in_sec = 2

	handler = SessionHandler(
		"testapp",
		maxSessionsPerIp=4,
		sessionMaxInactiveInterval=deletion_time_in_sec,
		sessionDeletionTimeout=23
	)

	try:
		for _ in range(sessionCount):
			handler.createSession()

		for _ in range(deletion_time_in_sec + 1):
			time.sleep(1)

		assert {} == handler.getSessions()
	finally:
		handler.deleteAllSessions()


def testGetSessionsByIP():
	handler = SessionHandler("testapp", maxSessionsPerIp=2)

	assert {} == handler.getSessions()

	session = handler.getSession(ip='12.34.56.78')
	assert {session.uid: session} == handler.getSessions()

	for _ in range(12):
		session = handler.createSession()

	assert len(handler.getSessions()) == 13
	assert {session.uid: session} == handler.getSessions(ip='12.34.56.78')

	newSession = handler.getSession(ip='12.34.56.78')
	assert newSession.ip == '12.34.56.78'

	with pytest.raises(OpsiAuthenticationError):
		handler.getSession(ip='12.34.56.78')


def testGettingSession(sessionHandler):
	session = sessionHandler.getSession()

	assert session.usageCount == 1


def testGettingSessionByUID(sessionHandler):
	session = sessionHandler.getSession(uid='testUID12345')

	assert session.usageCount == 1


def testGettingSessionByUIDAndReuse(sessionHandler):
	firstSession = sessionHandler.getSession(uid='testUID12345')

	assert firstSession.usageCount == 1

	secondSession = sessionHandler.getSession(uid=firstSession.uid)
	assert secondSession.usageCount == 2

	assert firstSession == secondSession


def testGettingNewSessionDoesNotSetUid(sessionHandler):
	session = sessionHandler.getSession(uid='testUID12345')

	assert session.uid != 'testUID12345'


def testGettingNewSessionDoesIgnoreSessionMarkedForDeletion(sessionHandler):
	session = sessionHandler.getSession()
	session.setMarkedForDeletion()

	secondSession = sessionHandler.getSession(uid=session.uid)
	assert secondSession != session
