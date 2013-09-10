#!/usr/bin/env python
#-*- coding: utf-8 -*-

import time
import unittest

from OPSI.Object import OpsiError, BackendError, OpsiClient, Host
from OPSI.Types import (forceObjectClass, forceUnicode, forceUnicodeList,
    forceList, forceBool, forceBoolList, forceInt, forceOct,
    forceOpsiTimestamp, forceHardwareAddress, forceHostId, forceIPAddress,
    forceNetworkAddress)


class OpsiErrorTestCase(unittest.TestCase):
    ERROR_ARGUMENT = None

    def setUp(self):
        self.error = OpsiError(self.ERROR_ARGUMENT)

    def tearDown(self):
        del self.error

    def testCanBePrinted(self):
        print(self.error)

    def testCanBeCaught(self):
        def raiseError():
            raise self.error

        self.assertRaises(OpsiError, raiseError)


class OpsiErrorWithIntTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = 1


class OpsiErrorWithBoolTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = True


class OpsiErrorWithTimeTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = time.localtime()


class OpsiErrorWithUnicodeStringTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = u'unicode string'


class OpsiErrorWithUTF8StringTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = u'utf-8 string: äöüß€'.encode('utf-8')


class OpsiErrorWithWindowsEncodedStringTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = u'windows-1258 string: äöüß€'.encode('windows-1258')


class OpsiErrorWithUTF16StringTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = u'utf-16 string: äöüß€'.encode('utf-16'),


class OpsiErrorWithLatin1StringTestCase(OpsiErrorTestCase):
    ERROR_ARGUMENT = u'latin1 string: äöüß'.encode('latin-1')


class BackendErrorTest(unittest.TestCase):
    def testIsSubClassOfOpsiError(self):
        def raiseError():
            raise BackendError('Test')

        self.assertRaises(OpsiError, raiseError)


class ForceObjectClassJSONTestCase(unittest.TestCase):
    def setUp(self):
        self.object = OpsiClient(
            id='test1.uib.local',
            description='Test client 1',
            notes='Notes ...',
            hardwareAddress='00:01:02:03:04:05',
            ipAddress='192.168.1.100',
            lastSeen='2009-01-01 00:00:00',
            opsiHostKey='45656789789012789012345612340123'
        )

        self.json = self.object.toJson()

    def tearDown(self):
        del self.json
        del self.object

    def testForceObjectClassToHostFromJSON(self):
        self.assertTrue(isinstance(forceObjectClass(self.json, Host), Host))

    def testForceObjectClassToOpsiClientFromJSON(self):
        self.assertTrue(isinstance(forceObjectClass(self.json, OpsiClient), OpsiClient))


class ForceObjectClassHashTestCase(unittest.TestCase):
    def setUp(self):
        self.object = OpsiClient(
            id='test1.uib.local',
            description='Test client 1',
            notes='Notes ...',
            hardwareAddress='00:01:02:03:04:05',
            ipAddress='192.168.1.100',
            lastSeen='2009-01-01 00:00:00',
            opsiHostKey='45656789789012789012345612340123'
        )

        self.hash = self.object.toHash()

    def tearDown(self):
        del self.hash
        del self.object

    def testForceObjectClassToHostFromHash(self):
        self.assertTrue(isinstance(forceObjectClass(self.hash, Host), Host))

    def testForceObjectClassToOpsiClientFromHash(self):
        self.assertTrue(isinstance(forceObjectClass(self.hash, OpsiClient), OpsiClient))


class ForceListTestCase(unittest.TestCase):
    def testForceListCreatesAListIfOnlyOneObjectIsGiven(self):
        self.assertEquals(forceList('x'), ['x'])


class ForceUnicodeTestCase(unittest.TestCase):
    def testForcingResultsInUnicode(self):
        self.assertTrue(type(forceUnicode('x')) is unicode)


class ForceUnicodeListTestCase(unittest.TestCase):
    def testForcingResultsInUnicode(self):
        for i in forceUnicodeList([None, 1, 'x', u'y']):
            self.assertTrue(type(i) is unicode)


class ForceBoolTestCase(unittest.TestCase):
    """
    Testing if forceBool works. Always should work case-insensitive.
    """
    def testOnOff(self):
        self.assertTrue(forceBool('on'))
        self.assertFalse(forceBool('OFF'))

    def testYesNo(self):
        self.assertTrue(forceBool('YeS'))
        self.assertFalse(forceBool('no'))

    def testOneZero(self):
        self.assertTrue(forceBool(1))
        self.assertTrue(forceBool('1'))
        self.assertFalse(forceBool(0))
        self.assertFalse(forceBool('0'))

    def testXMarksTheSpot(self):
        self.assertTrue(forceBool(u'x'))

    def testBoolTypes(self):
        self.assertTrue(forceBool(True))
        self.assertFalse(forceBool(False))


class ForceBoolListTestCase(unittest.TestCase):
    def testPositiveList(self):
        for i in forceBoolList([1, 'yes', 'on', '1', True]):
            self.assertTrue(i)

    def testMethod(self):
        for i in forceBoolList([None, 'no', 'false', '0', False]):
            self.assertFalse(i)


class ForceIntTestCase(unittest.TestCase):
    def testWithString(self):
        self.assertEquals(forceInt('100'), 100)

    def testWithNegativeValueInString(self):
        self.assertEquals(forceInt('-100'), -100)

    def testWithLongValue(self):
        self.assertEquals(forceInt(long(1000000000000000)), 1000000000000000)

    def testRaisingValueError(self):
        self.assertRaises(ValueError, forceInt, 'abc')


class ForceOctTestCase(unittest.TestCase):
    def testForcingDoesNotChangeValue(self):
        self.assertEquals(forceOct(0666), 0666)

    def testForcingString(self):
        self.assertEquals(forceOct('666'), 0666)

    def testForcingStringWithLeadingZero(self):
        self.assertEquals(forceOct('0666'), 0666)

    def testRaisingErrors(self):
        self.assertRaises(ValueError, forceOct, 'abc')


class ForceTimeStampTestCase(unittest.TestCase):
    def testForcingReturnsString(self):
        self.assertEquals(forceOpsiTimestamp('20000202111213'), u'2000-02-02 11:12:13')

    def testResultIsUnicode(self):
        self.assertTrue(type(forceOpsiTimestamp('2000-02-02 11:12:13')) is unicode)

    def testRaisingErrorsOnWrongInput(self):
        self.assertRaises(ValueError, forceOpsiTimestamp, 'abc')

class ForceHostIdTestCase(unittest.TestCase):
    def testForcingWithValidId(self):
        self.assertEquals(forceHostId(u'client.uib.local'), u'client.uib.local')
        self.assertTrue(forceHostId(u'client.uib.local'), u'client.uib.local')

    def testInvalidHOstIdsRaiseExceptions(self):
        self.assertRaises(ValueError, forceHostId, 'abc')
        self.assertRaises(ValueError, forceHostId, 'abc.def')
        self.assertRaises(ValueError, forceHostId, '.uib.local')
        self.assertRaises(ValueError, forceHostId, 'abc.uib.x')


class ForceHardwareAddressTestCase(unittest.TestCase):
    def testForcingReturnsAddressSeperatedByColons(self):
        self.assertEquals(forceHardwareAddress('12345678ABCD'), u'12:34:56:78:ab:cd')
        self.assertEquals(forceHardwareAddress('12:34:56:78:ab:cd'), u'12:34:56:78:ab:cd')

    def testForcingReturnsLowercaseLetters(self):
        self.assertEquals(forceHardwareAddress('12-34-56-78-Ab-cD'), u'12:34:56:78:ab:cd')
        self.assertEquals(forceHardwareAddress('12-34-56:78AB-CD'), u'12:34:56:78:ab:cd')

    def testForcingResultsInUnicode(self):
        self.assertTrue(type(forceHardwareAddress('12345678ABCD')) is unicode)

    def testForcingInvalidAddressesRaiseExceptions(self):
        self.assertRaises(ValueError, forceHardwareAddress, '12345678abc')
        self.assertRaises(ValueError, forceHardwareAddress, '12345678abcdef')
        self.assertRaises(ValueError, forceHardwareAddress, '1-2-3-4-5-6-7')
        self.assertRaises(ValueError, forceHardwareAddress, None)
        self.assertRaises(ValueError, forceHardwareAddress, True)


class ForceIPAdressTestCase(unittest.TestCase):
    def testForcing(self):
        self.assertEquals(forceIPAddress('192.168.101.1'), u'192.168.101.1')

    def testForcingReturnsUnicode(self):
        self.assertTrue(type(forceIPAddress('1.1.1.1')) is unicode)

    def testForcingWithInvalidAddressesRaisesExceptions(self):
        self.assertRaises(ValueError, forceIPAddress, '1922.1.1.1')
        self.assertRaises(ValueError, forceIPAddress, None)
        self.assertRaises(ValueError, forceIPAddress, True)
        self.assertRaises(ValueError, forceIPAddress, '1.1.1.1.')
        self.assertRaises(ValueError, forceIPAddress, '2.2.2.2.2')
        self.assertRaises(ValueError, forceIPAddress, 'a.2.3.4')


class ForceNetworkAddressTestCase(unittest.TestCase):
    def testForcing(self):
        self.assertEquals(forceNetworkAddress('192.168.0.0/16'), u'192.168.0.0/16')

    def testForcingReturnsUnicode(self):
        self.assertTrue(type(forceNetworkAddress('10.10.10.10/32')) is unicode)

    def testForcingWithInvalidAddressesRaisesExceptions(self):
        self.assertRaises(ValueError, forceNetworkAddress, '192.168.101.1')
        self.assertRaises(ValueError, forceNetworkAddress, '192.1.1.1/40')
        self.assertRaises(ValueError, forceNetworkAddress, None)
        self.assertRaises(ValueError, forceNetworkAddress, True)
        self.assertRaises(ValueError, forceNetworkAddress, '10.10.1/24')
        self.assertRaises(ValueError, forceNetworkAddress, 'a.2.3.4/0')


# for i in ('file:///', 'file:///path/to/file', 'smb://server/path', 'https://x:y@server.domain.tld:4447/resource'):
#     assert forceUrl(i) == i
#     assert type(forceUrl(i)) is unicode
# for i in ('abc', '/abc', 'http//server', 1, True, None):
#     try:
#         forceUrl(i)
#     except ValueError:
#         pass
#     else:
#         raise Exception(u"'%s' was accepted as Url" % i)

# assert forceOpsiHostKey('abCdeF78901234567890123456789012') == 'abcdef78901234567890123456789012'
# assert type(forceOpsiHostKey('12345678901234567890123456789012')) is unicode
# for i in ('abCdeF7890123456789012345678901', 'abCdeF78901234567890123456789012b', 'GbCdeF78901234567890123456789012'):
#     try:
#         forceOpsiHostKey(i)
#     except ValueError:
#         pass
#     else:
#         raise Exception(u"'%s' was accepted as OpsiHostKey" % i)

# assert forceProductVersion('1.0') == '1.0'
# assert type(forceProductVersion('1.0')) is unicode

# assert forcePackageVersion(1) == '1'
# assert type(forceProductVersion('8')) is unicode

# assert forceProductId('testProduct1') == 'testproduct1'
# assert type(forceProductId('test-Product-1')) is unicode
# for i in (u'äöü', 'product test'):
#     try:
#         forceProductId(i)
#     except ValueError:
#         pass
#     else:
#         raise Exception(u"'%s' was accepted as ProductId" % i)

# assert forceFilename('c:\\tmp\\test.txt') == u'c:\\tmp\\test.txt'
# assert type(forceFilename('/tmp/test.txt')) is unicode


# for i in ('installed', 'not_installed'):
#     assert forceInstallationStatus(i) == i
#     assert type(forceInstallationStatus(i)) is unicode
# for i in ('none', 'abc'):
#     try:
#         forceInstallationStatus(i)
#     except ValueError:
#         pass
#     else:
#         raise Exception(u"'%s' was accepted as installationStatus" % i)

# for i in ('setup', 'uninstall', 'update', 'once', 'always', 'none', None):
#     assert forceActionRequest(i) == str(i).lower()
#     assert type(forceActionRequest(i)) is unicode
# for i in ('installed'):
#     try:
#         forceActionRequest(i)
#     except ValueError:
#         pass
#     else:
#         raise Exception(u"'%s' was accepted as actionRequest" % i)


# assert forceActionProgress('installing 50%') == u'installing 50%'
# assert type(forceActionProgress('installing 50%')) is unicode


# assert forceLanguageCode('dE') == u'de'
# assert forceLanguageCode('en-us') == u'en-US'
# try:
#     forceLanguageCode('de-DEU')
# except ValueError:
#     pass
# else:
#     raise Exception(u"'de-DEU' was accepted as languageCode")
# assert forceLanguageCode('xx-xxxx-xx') == u'xx-Xxxx-XX'
# assert forceLanguageCode('yy_yy') == u'yy-YY'
# assert forceLanguageCode('zz_ZZZZ') == u'zz-Zzzz'


# assert forceArchitecture('X86') == u'x86'
# assert forceArchitecture('X64') == u'x64'

# forceTime(time.time())
# forceTime(time.localtime())

# assert forceEmailAddress('info@uib.de') == u'info@uib.de'
# try:
#     forceEmailAddress('infouib.de')
# except ValueError:
#     pass
# else:
#     raise Exception(u"'infouib.de' was accepted as a-mail address")


# getPossibleClassAttributes(Host)

# obj1 = OpsiConfigserver(
#     id                  = 'configserver1.uib.local',
#     opsiHostKey         = '71234545689056789012123678901234',
#     depotLocalUrl       = 'file:///opt/pcbin/install',
#     depotRemoteUrl      = u'smb://configserver1/opt_pcbin/install',
#     repositoryLocalUrl  = 'file:///var/lib/opsi/repository',
#     repositoryRemoteUrl = u'webdavs://configserver1:4447/repository',
#     description         = 'The configserver',
#     notes               = 'Config 1',
#     hardwareAddress     = None,
#     ipAddress           = None,
#     inventoryNumber     = '00000000001',
#     networkAddress      = '192.168.1.0/24',
#     maxBandwidth        = 10000
# )

# obj2 = OpsiConfigserver(
#     id                  = 'configserver1.uib.local',
#     opsiHostKey         = '71234545689056789012123678901234',
#     depotLocalUrl       = 'file:///opt/pcbin/install',
#     depotRemoteUrl      = u'smb://configserver1/opt_pcbin/install',
#     repositoryLocalUrl  = 'file:///var/lib/opsi/repository',
#     repositoryRemoteUrl = u'webdavs://configserver1:4447/repository',
#     description         = 'The configserver',
#     notes               = 'Config 1',
#     hardwareAddress     = None,
#     ipAddress           = None,
#     inventoryNumber     = '00000000001',
#     networkAddress      = '192.168.1.0/24',
#     maxBandwidth        = 10000
# )

# assert obj1 == obj2
# obj2 = obj1
# assert obj1 == obj2

# obj2 = OpsiDepotserver(
#     id                  = 'depotserver1.uib.local',
#     opsiHostKey         = '19012334567845645678901232789012',
#     depotLocalUrl       = 'file:///opt/pcbin/install',
#     depotRemoteUrl      = 'smb://depotserver1.uib.local/opt_pcbin/install',
#     repositoryLocalUrl  = 'file:///var/lib/opsi/repository',
#     repositoryRemoteUrl = 'webdavs://depotserver1.uib.local:4447/repository',
#     description         = 'A depot',
#     notes               = 'D€pot 1',
#     hardwareAddress     = None,
#     ipAddress           = None,
#     inventoryNumber     = '00000000002',
#     networkAddress      = '192.168.2.0/24',
#     maxBandwidth        = 10000
# )
# assert obj1 != obj2

# obj2 = {"test": 123}
# assert obj1 != obj2

# obj1 = LocalbootProduct(
#     id                 = 'product2',
#     name               = u'Product 2',
#     productVersion     = '2.0',
#     packageVersion     = 'test',
#     licenseRequired    = False,
#     setupScript        = "setup.ins",
#     uninstallScript    = u"uninstall.ins",
#     updateScript       = "update.ins",
#     alwaysScript       = None,
#     onceScript         = None,
#     priority           = 0,
#     description        = None,
#     advice             = "",
#     productClassIds    = ['localboot-products'],
#     windowsSoftwareIds = ['{98723-7898adf2-287aab}', 'xxxxxxxx']
# )
# obj2 = LocalbootProduct(
#     id                 = 'product2',
#     name               = u'Product 2',
#     productVersion     = '2.0',
#     packageVersion     = 'test',
#     licenseRequired    = False,
#     setupScript        = "setup.ins",
#     uninstallScript    = u"uninstall.ins",
#     updateScript       = "update.ins",
#     alwaysScript       = None,
#     onceScript         = None,
#     priority           = 0,
#     description        = None,
#     advice             = "",
#     productClassIds    = ['localboot-products'],
#     windowsSoftwareIds = ['xxxxxxxx', '{98723-7898adf2-287aab}']
# )
# assert obj1 == obj2



