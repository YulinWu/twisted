
# Twisted, the Framework of Your Internet
# Copyright (C) 2001 Matthew W. Lefkowitz
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of version 2.1 of the GNU Lesser General Public
# License as published by the Free Software Foundation.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA


# System Imports
import string
import types

# Twisted Imports
from twisted.spread import pb
from twisted.internet import passport
from twisted.python import log, roots
from twisted.manhole import coil
from twisted.persisted import styles
from twisted import copyright

# Status "enumeration"

OFFLINE = 0
ONLINE  = 1
AWAY = 2

statuses = ["Offline","Online","Away"]

class WordsError(pb.Error):
    pass

class NotInCollectionError(WordsError):
    pass

class NotInGroupError(NotInCollectionError):
    def __init__(self, groupName, pName=None):
        WordsError.__init__(self, groupName, pName)
        self.group = groupName
        self.pName = pName

    def __str__(self):
        if self.pName:
            pName = "'%s' is" % (self.pName,)
        else:
            pName = "You are"
        s = ("%s not in group '%s'." % (pName, self.group))
        return s

class UserNonexistantError(NotInCollectionError):
    def __init__(self, pName):
        WordsError.__init__(self, pName)
        self.pName = pName

    def __str__(self):
        return "'%s' does not exist." % (self.pName,)

class WrongStatusError(WordsError):
    def __init__(self, status, pName=None):
        WordsError.__init__(self, status, pName)
        self.status = status
        self.pName = pName

    def __str__(self):
        if self.pName:
            pName = "'%s'" % (self.pName,)
        else:
            pName = "User"

        if self.status in statuses:
            status = self.status
        else:
            status = 'unknown? (%s)' % self.status
        s = ("%s status is '%s'." % (pName, status))
        return s


class WordsClientInterface:
    """A client to a perspective on the twisted.words service.

    I attach to that participant with Participant.attached(),
    and detatch with Participant.detached().
    """

    def receiveContactList(self, contactList):
        """Receive a list of contacts and their status.

        The list is composed of 2-tuples, of the form
        (contactName, contactStatus)
        """

    def notifyStatusChanged(self, name, status):
        """Notify me of a change in status of one of my contacts.
        """

    def receiveGroupMembers(self, names, group):
        """Receive a list of members in a group.

        'names' is a list of participant names in the group named 'group'.
        """

    def receiveDirectMessage(self, sender, message, metadata=None):
        """Receive a message from someone named 'sender'.
        'metadata' is a dict of special flags. So far 'style': 'emote'
        is defined. Note that 'metadata' *must* be optional.
        """

    def receiveGroupMessage(self, sender, group, message, metadata=None):
        """Receive a message from 'sender' directed to a group.
        'metadata' is a dict of special flags. So far 'style': 'emote'
        is defined. Note that 'metadata' *must* be optional.
        """

    def memberJoined(self, member, group):
        """Tells me a member has joined a group.
        """

    def memberLeft(self, member, group):
        """Tells me a member has left a group.
        """


class Participant(pb.Perspective, styles.Versioned):
    def __init__(self, name):
        pb.Perspective.__init__(self, name)
        self.name = name
        self.status = OFFLINE
        self.contacts = []
        self.reverseContacts = []
        self.groups = []
        self.client = None
        self.info = ""

    persistenceVersion = 1

    def __getstate__(self):
        state = styles.Versioned.__getstate__(self)
        # Assumptions:
        # * self.client is a RemoteReference, or otherwise represents
        #   a transient presence.
        state["client"] = None
        # * Because we have no client, we are not online.
        state["status"] = OFFLINE
        # * Because we are not online, we are in no groups.
        state["groups"] = []

        return state

    def attached(self, client, identity):
        """Attach a client which implements WordsClientInterface to me.
        """
        if ((self.client is not None)
            and self.client.__class__ != styles.Ephemeral):
            raise passport.Unauthorized("duplicate login not permitted.")
        log.msg("attached: %s" % self.name)
        self.client = client
        client.receiveContactList(map(lambda contact: (contact.name,
                                                       contact.status),
                                      self.contacts))
        self.changeStatus(ONLINE)
        return self

    def changeStatus(self, newStatus):
        self.status = newStatus
        for contact in self.reverseContacts:
            contact.notifyStatusChanged(self)

    def notifyStatusChanged(self, contact):
        if self.client:
            self.client.notifyStatusChanged(contact.name, contact.status)

    def detached(self, client, identity):
        log.msg("detached: %s" % self.name)
        self.client = None
        for group in self.groups[:]:
            try:
                self.leaveGroup(group.name)
            except NotInGroupError:
                pass
        self.changeStatus(OFFLINE)

    def addContact(self, contactName):
        # XXX This should use a database or something.  Doing it synchronously
        # like this won't work.
        contact = self.service.getPerspectiveNamed(contactName)
        self.contacts.append(contact)
        contact.reverseContacts.append(self)
        self.notifyStatusChanged(contact)

    def removeContact(self, contactName):
        for contact in self.contacts:
            if contact.name == contactName:
                self.contacts.remove(contact)
                contact.reverseContacts.remove(self)
                return
        raise NotInCollectionError("No such contact '%s'."
                                   % (contactName,))

    def joinGroup(self, name):
        group = self.service.getGroup(name)
        if group in self.groups:
            # We're in that group.  Don't make a fuss.
            return
        group.addMember(self)
        self.groups.append(group)

    def leaveGroup(self, name):
        for group in self.groups:
            if group.name == name:
                self.groups.remove(group)
                group.removeMember(self)
                return
        raise NotInGroupError(name)

    def getGroupMembers(self, groupName):
        for group in self.groups:
            if group.name == groupName:
                self.client.receiveGroupMembers(map(lambda m: m.name,
                                                    group.members),
                                                group.name)
        raise NotInGroupError(groupName)

    def getGroupMetadata(self, groupName):
        for group in self.groups:
            if group.name == groupName:
                self.client.setGroupMetadata(group.metadata, group.name)

    def receiveDirectMessage(self, sender, message, metadata):
        if self.client:
            if metadata:
                d = self.client.receiveDirectMessage(sender.name, message,
                                                     metadata)
                #If the client doesn't support metadata, call this function
                #again with no metadata, so none is sent
                #
                #note on the 'if d:' - the IRC service is in-process and
                #won't return a Deferred, but rather it returns None.
                #silently ignore it. (This is really evil and terrible.)
                if d:
                    d.addErrback(self.receiveDirectMessage,
                                 sender.name, message, None)
            else:
                self.client.receiveDirectMessage(sender.name, message)
        else:
            raise WrongStatusError(self.status, self.name)


    def receiveGroupMessage(self, sender, group, message, metadata):
        if sender is not self and self.client:
            if metadata:
                d = self.client.receiveGroupMessage(sender.name, group.name,
                                                    message, metadata)
                if d:
                    d.addErrback(self.receiveGroupMessage,
                                 sender, group, message, None)
            else:
                self.client.receiveGroupMessage(sender.name, group.name,
                                                message)

    def memberJoined(self, member, group):
        self.client.memberJoined(member.name, group.name)

    def memberLeft(self, member, group):
        self.client.memberLeft(member.name, group.name)

    def directMessage(self, recipientName, message, metadata=None):
        # XXX getPerspectiveNamed is misleading here -- this ought to look up
        # the user to make sure they're *online*, and if they're not, it may
        # need to query a database.
        recipient = self.service.getPerspectiveNamed(recipientName)
        recipient.receiveDirectMessage(self, message, metadata or {})

    def groupMessage(self, groupName, message, metadata=None):
        for group in self.groups:
            if group.name == groupName:
                group.sendMessage(self, message, metadata or {})
                return
        raise NotInGroupError(groupName)

    def setGroupMetadata(self, dict_, groupName):
        if self.client:
            self.client.setGroupMetadata(dict_, groupName)

    # Establish client protocol for PB.
    perspective_changeStatus = changeStatus
    perspective_joinGroup = joinGroup
    perspective_directMessage = directMessage
    perspective_addContact = addContact
    perspective_removeContact = removeContact
    perspective_groupMessage = groupMessage
    perspective_leaveGroup = leaveGroup
    perspective_getGroupMembers = getGroupMembers

    def __repr__(self):
        if self.identityName != "Nobody":
            id_s = '(id:%s)' % (self.identityName, )
        else:
            id_s = ''
        s = ("<%s '%s'%s on %s at %x>"
             % (self.__class__, self.name, id_s,
                self.service.serviceName, id(self)))
        return s

class Group(styles.Versioned):

    def __init__(self, name):
        self.name = name
        self.members = []
        self.metadata = {'topic': 'Welcome to %s!' % self.name,
                         'topic_author': 'admin'}

    def __getstate__(self):
        state = styles.Versioned.__getstate__(self)
        state['members'] = []
        return state

    def addMember(self, participant):
        if participant in self.members:
            return
        for member in self.members:
            member.memberJoined(participant, self)
        participant.setGroupMetadata(self.metadata, self.name)
        self.members.append(participant)

    def removeMember(self, participant):
        try:
            self.members.remove(participant)
        except ValueError:
            raise NotInGroupError(self.name, participant.name)
        else:
            for member in self.members:
                member.memberLeft(participant, self)

    def sendMessage(self, sender, message, metadata):
        for member in self.members:
            member.receiveGroupMessage(sender, self, message, metadata)

    def setMetadata(self, dict_):
        self.metadata.update(dict_)
        for member in self.members:
            member.setGroupMetadata(dict_, self.name)

    def __repr__(self):
        s = "<%s '%s' at %x>" % (self.__class__, self.name, id(self))
        return s


    ##Persistence Versioning

    persistenceVersion = 1

    def upgradeToVersion1(self):
        self.metadata = {'topic': self.topic}
        del self.topic
        self.metadata['topic_author'] = 'admin'

class Service(pb.Service, styles.Versioned, coil.Configurable):
    """I am a chat service.
    """
    def __init__(self, name, app):
        pb.Service.__init__(self, name, app)
        self.participants = {}
        self.groups = {}
        self._setConfigDispensers()

    # Configuration stuff.
    def _setConfigDispensers(self):
        import ircservice, webwords
        self.configDispensers = [
            ['makeIRCGateway', ircservice.IRCGateway, "IRC chat gateway to %s" % self.serviceName],
            ['makeWebAccounts', webwords.WordsGadget, "Public Words Website for %s" % self.serviceName]
            ]

    def makeWebAccounts(self):
        import webwords
        return webwords.WordsGadget(self)

    def makeIRCGateway(self):
        import ircservice
        return ircservice.IRCGateway(self)

    def configInit(self, container, name):
        self.__init__(name, container.app)

    def getConfiguration(self):
        return {"name": self.serviceName}

    configTypes = {
        'name': types.StringType
        }

    configName = 'Twisted Words PB Service'

    def config_name(self, name):
        raise coil.InvalidConfiguration("You can't change a Service's name.")

    ## Persistence versioning.
    persistenceVersion = 2

    def upgradeToVersion1(self):
        from twisted.internet.app import theApplication
        styles.requireUpgrade(theApplication)
        pb.Service.__init__(self, 'twisted.words', theApplication)

    def upgradeToVersion2(self):
        self._setConfigDispensers()

    ## Service functionality.

    def getGroup(self, name):
        group = self.groups.get(name)
        if not group:
            group = Group(name)
            self.groups[name] = group
        return group

    def createParticipant(self, name):
        if not self.participants.has_key(name):
            log.msg("Created New Participant: %s" % name)
            p = Participant(name)
            p.setService(self)
            self.participants[name] = p
            return p

    def getPerspectiveNamed(self, name):
        try:
            p = self.participants[name]
        except KeyError:
            raise UserNonexistantError(name)
        else:
            return p

    def __str__(self):
        s = "<%s in app '%s' at %x>" % (self.serviceName,
                                        self.application.name,
                                        id(self))
        return s

coil.registerClass(Service)
