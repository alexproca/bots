import os
import sys
import posixpath
import time
import datetime
import glob
import shutil
import fnmatch
import zipfile
if os.name == 'nt':
    import msvcrt
elif os.name == 'posix':
    import fcntl
try:
    import cPickle as pickle
except ImportError:
    import pickle
try:
    import json as simplejson
except ImportError:
    import simplejson
import email
import email.Utils
import email.Generator
import email.Message
import email.header
import email.encoders
import smtplib
import ftplib
import socket
import ssl
from django.utils.translation import ugettext as _
#Bots modules
import botslib
import botsglobal
from botsconfig import *

@botslib.log_session
def run(idchannel,command,idroute,rootidta=None):
    '''run a communication session (dispatcher for communication functions).'''
    if rootidta is None:
        rootidta = botsglobal.currentrun.get_minta4query()
    for row in botslib.query('''SELECT *
                                FROM channel
                                WHERE idchannel=%(idchannel)s''',
                                {'idchannel':idchannel}):
        channeldict = dict(row)   #convert to real dictionary ()
        botsglobal.logger.debug(u'Start communication channel "%(idchannel)s" type %(type)s %(inorout)s.',channeldict)
        #for acceptance testing bots has an option to turn of external communication in channels
        if botsglobal.ini.getboolean('acceptance','runacceptancetest',False):
            #override values in channels for acceptance testing.
            #testpath is used to 'trigger' this: if testpath has value, use acceptance.
            if channeldict['testpath']:
                channeldict['path'] = channeldict['testpath']    #use the testpath to specify where to find acceptance  tests.
                channeldict['remove'] = False    #never remove during acceptance testing
                if channeldict['type'] in ['file','mimefile','trash']:
                    pass #do nothing, same type
                elif channeldict['type'] in ['smtp','smtps','smtpstarttls','pop3','pop3s','pop3apop','imap4','imap4s']:
                    channeldict['type'] = 'mimefile'
                else:   #channeldict['type'] in ['ftp','ftps','ftpis','sftp','xmlrpc','ftp','ftp','communicationscript','db',]
                    channeldict['type'] = 'file'
            botsglobal.logger.debug(u'Channel "%(idchannel)s" adapted for acceptance test: type "%(type)s", testpath "%(testpath)s".',channeldict)
                
        #update communication/run process with idchannel
        ta_run = botslib.OldTransaction(botslib._Transaction.processlist[-1])
        if channeldict['inorout'] == 'in':
            ta_run.update(fromchannel=channeldict['idchannel'])
        else:
            ta_run.update(tochannel=channeldict['idchannel'])

        try:
            userscript,scriptname = botslib.botsimport('communicationscripts',channeldict['idchannel'])
        except ImportError:       #communicationscript is not there; other errors like syntax errors are not catched
            userscript = scriptname = None
        #get the communication class to use:
        if userscript and hasattr(userscript,channeldict['type']):          #check communication class in userscript (sub classing)
            classtocall = getattr(userscript,channeldict['type'])
        elif userscript and hasattr(userscript,'UserCommunicationClass'):   #check for communication class called 'UserCommunicationClass' in userscript. 20110920: Obsolete, depreciated. Keep this for now.
            classtocall = getattr(userscript,'UserCommunicationClass')       #20130206: does have advantages...liek for testing etc (no dependent upon type)
        else:
            classtocall = globals()[channeldict['type']]                    #get the communication class from this module

        comclass = classtocall(channeldict,idroute,userscript,scriptname,command,rootidta) #call the class for this type of channel
        comclass.run()
        botsglobal.logger.debug(u'Finished communication channel "%(idchannel)s" type %(type)s %(inorout)s.',channeldict)
        break   #there can only be one channel; this break takes care that if found, the 'else'-clause is skipped
    else:
        raise botslib.CommunicationError(_(u'Channel "%(idchannel)s" is unknown.'),{'idchannel':idchannel})


class _comsession(object):
    ''' Abstract class for communication-session. Use only subclasses.
        Subclasses are called by dispatcher function 'run'
        Often 'idroute' is passed as a parameter. This is ONLY because of the @botslib.log_session-wrapper!
        use self.idroute!!
    '''
    def __init__(self,channeldict,idroute,userscript,scriptname,command,rootidta):
        self.channeldict = channeldict
        self.idroute = idroute
        self.userscript = userscript
        self.scriptname = scriptname
        self.command = command
        self.rootidta = rootidta

    def run(self):
        if self.channeldict['inorout'] == 'out':
            self.precommunicate()
            self.connect()
            self.outcommunicate()
            self.disconnect()
            self.archive()
        else:   #incommunication
            if self.command == 'new': #only in-communicate for new run
                #handle maxsecondsperchannel: use global value from bots.ini unless specified in channel. (In database this is field 'rsrv2'.)
                self.maxsecondsperchannel = botsglobal.ini.getint('settings','maxsecondsperchannel',sys.maxint) if self.channeldict['rsrv2'] <= 0 else self.channeldict['rsrv2']
                self.connect()
                self.incommunicate()
                self.disconnect()
            self.postcommunicate()
            self.archive()

    def archive(self):
        ''' after the communication channel has ran, archive received of send files.
            archivepath is the root directory for the archive (for this channel).
            within the archivepath files are stored by default as [archivepath]/[date]/[unique_filename]
            
        '''
        if not self.channeldict['archivepath']:
            return  #do not archive if not indicated
        if botsglobal.ini.getboolean('acceptance','runacceptancetest',False): 
            return  #do not archive in acceptance testing
        if self.channeldict['filename'] and self.channeldict['type'] in ('file','ftp','ftps','ftpis','sftp','mimefile','communicationscript'):
            archiveexternalname = botsglobal.ini.getboolean('settings','archiveexternalname',False) #use external filename in archive 
        else:
            archiveexternalname = False
        if self.channeldict['inorout'] == 'in':
            status = FILEIN
            statust = OK
            channel = 'fromchannel'
        else:
            if archiveexternalname:
                status = EXTERNOUT
            else:
                status = FILEOUT
            statust = DONE
            channel = 'tochannel'
        #user script can manipulate archivepath
        if self.userscript and hasattr(self.userscript,'archivepath'):
            archivepath = botslib.runscript(self.userscript,self.scriptname,'archivepath',channeldict=self.channeldict)
        else:
            archivepath = botslib.join(self.channeldict['archivepath'],time.strftime('%Y%m%d'))
        archivezip = botsglobal.ini.getboolean('settings','archivezip',False)   #archive to zip or not
        if archivezip:
            archivepath += '.zip'
        checkedifarchivepathisthere = False  #for a outchannel that is less used, lots of empty dirs will be created. This var is used to check within loop if dir exist, but this is only checked one time.
        for row in botslib.query('''SELECT filename,idta
                                    FROM  ta
                                    WHERE idta>%(rootidta)s
                                    AND   status=%(status)s
                                    AND   statust=%(statust)s
                                    AND   ''' + channel + '''=%(idchannel)s
                                    ''',
                                    {'idchannel':self.channeldict['idchannel'],'status':status,
                                    'statust':statust,'rootidta':self.rootidta}):
            if not checkedifarchivepathisthere:
                if archivezip:
                    botslib.dirshouldbethere(os.path.dirname(archivepath))
                    archivezipfilehandler = zipfile.ZipFile(archivepath,'a',zipfile.ZIP_DEFLATED)
                else:
                    botslib.dirshouldbethere(archivepath)
                checkedifarchivepathisthere = True

            if archiveexternalname:
                if self.channeldict['inorout'] == 'in':
                    # we have internal filename, get external
                    absfilename = botslib.abspathdata(row['filename'])
                    taparent = botslib.OldTransaction(idta=row['idta'])
                    ta_list = botslib.trace_origin(ta=taparent,where={'status':EXTERNIN})
                    if ta_list:
                        archivename = os.path.basename(ta_list[-1].filename)
                    else:
                        archivename = row['filename']
                else:
                    # we have external filename, get internal
                    archivename = os.path.basename(row['filename'])
                    taparent = botslib.OldTransaction(idta=row['idta'])
                    ta_list = botslib.trace_origin(ta=taparent,where={'status':FILEOUT})
                    absfilename = botslib.abspathdata(ta_list[0].filename)
            else:
                # use internal name in archive
                absfilename = botslib.abspathdata(row['filename'])
                archivename = os.path.basename(row['filename'])

            if self.userscript and hasattr(self.userscript,'archivename'):
                archivename = botslib.runscript(self.userscript,self.scriptname,'archivename',channeldict=self.channeldict,idta=row['idta'],filename=absfilename)
            #~print 'archive',os.path.basename(absfilename),'as',archivename

            if archivezip:
                archivezipfilehandler.write(absfilename,archivename)
            else:
                # if a file of the same name already exists, add a timestamp
                if os.path.isfile(botslib.join(archivepath,archivename)):
                    archivename = os.path.splitext(archivename)[0] + time.strftime('_%H%M%S') + os.path.splitext(archivename)[1]
                shutil.copy(absfilename,botslib.join(archivepath,archivename))

        if archivezip and checkedifarchivepathisthere:
            archivezipfilehandler.close()


    def postcommunicate(self):
        pass

    def precommunicate(self):
        pass

    def file2mime(self):
        ''' convert 'plain' files into email (mime-document).
            1 edi file always in 1 mail.
            from status FILEOUT to FILEOUT
        '''
        #select files with right statust, status and channel.
        for row in botslib.query('''SELECT idta,filename,frompartner,topartner,charset,contenttype,editype
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                    AND status=%(status)s
                                    AND statust=%(statust)s
                                    AND tochannel=%(idchannel)s
                                    ''',
                                    {'idchannel':self.channeldict['idchannel'],'status':FILEOUT,
                                    'statust':OK,'idroute':self.idroute,'rootidta':self.rootidta}):
            try:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to = ta_from.copyta(status=FILEOUT)
                ta_to.synall()  #needed for user exits: get all parameters of ta_to from database;
                confirmtype = u''
                confirmasked = False
                charset = row['charset']

                if row['editype'] == 'email-confirmation': #outgoing MDN: message is already assembled
                    outfilename = row['filename']
                else:   #assemble message: headers and payload. Bots uses simple MIME-envelope; by default payload is an attachment
                    message = email.Message.Message()
                    #set 'from' header (sender)
                    frommail,ccfrom_not_used_variable = self.idpartner2mailaddress(row['frompartner'])    #lookup email address for partnerID
                    message.add_header('From', frommail)

                    #set 'to' header (receiver)
                    if self.userscript and hasattr(self.userscript,'getmailaddressforreceiver'):    #user exit to determine to-address/receiver
                        tomail,ccto = botslib.runscript(self.userscript,self.scriptname,'getmailaddressforreceiver',channeldict=self.channeldict,ta=ta_to)
                    else:
                        tomail,ccto = self.idpartner2mailaddress(row['topartner'])          #lookup email address for partnerID
                    message.add_header('To',tomail)
                    if ccto:
                        message.add_header('CC',ccto)

                    if botsglobal.ini.getboolean('acceptance','runacceptancetest',False):
                        reference = '123message-ID email should be unique123'
                        email_datetime = email.Utils.formatdate(timeval=time.mktime(time.strptime("2013-01-23 01:23:45", "%Y-%m-%d %H:%M:%S")),localtime=True)
                    else:
                        reference = email.Utils.make_msgid(str(ta_to.idta))    #use transaction idta in message id.
                        email_datetime = email.Utils.formatdate(localtime=True)
                    message.add_header('Message-ID',reference)
                    message.add_header("Date",email_datetime)
                    ta_to.update(frommail=frommail,tomail=tomail,cc=ccto,reference=reference)   #update now (in order to use correct & updated ta_to in userscript)

                    #set Disposition-Notification-To: ask/ask not a a MDN?
                    if botslib.checkconfirmrules('ask-email-MDN',idroute=self.idroute,idchannel=self.channeldict['idchannel'],
                                                                frompartner=row['frompartner'],topartner=row['topartner']):
                        message.add_header("Disposition-Notification-To",frommail)
                        confirmtype = u'ask-email-MDN'
                        confirmasked = True

                    #set subject
                    subject = str(row['idta'])
                    content = botslib.readdata(row['filename'])     #get attachment from data file
                    if self.userscript and hasattr(self.userscript,'subject'):    #user exit to determine subject
                        subject = botslib.runscript(self.userscript,self.scriptname,'subject',channeldict=self.channeldict,ta=ta_to,subjectstring=subject,content=content)
                    message.add_header('Subject',subject)

                    #set MIME-version
                    message.add_header('MIME-Version','1.0')

                    #set attachment filename
                    filename_mask = self.channeldict['filename'] if self.channeldict['filename'] else '*'
                    attachmentfilename = self.filename_formatter(filename_mask,ta_to)
                    if attachmentfilename and self.channeldict['sendmdn'] != 'body':  #if not explicitly indicated 'as body' or (old)  if attachmentfilename is None or empty string: do not send as an attachment.
                        message.add_header("Content-Disposition",'attachment',filename=attachmentfilename)

                    #set Content-Type and charset
                    charset = self.convertcodecformime(row['charset'])
                    message.add_header('Content-Type',row['contenttype'].lower(),charset=charset)          #contenttype is set in grammar.syntax

                    #set attachment/payload; the Content-Transfer-Encoding is set by python encoder
                    message.set_payload(content)   #do not use charset; this lead to unwanted encodings...bots always uses base64
                    if self.channeldict['askmdn'] == 'never':       #channeldict['askmdn'] is the Mime encoding
                        email.encoders.encode_7or8bit(message)      #no encoding; but the Content-Transfer-Encoding is set to 7-bit or 8-bt
                    elif self.channeldict['askmdn'] == 'ascii' and charset == 'us-ascii':
                        pass        #do nothing: ascii is default encoding
                    else:           #if Mime encoding is 'always' or  (Mime encoding == 'ascii' and charset!='us-ascii'): use base64
                        email.encoders.encode_base64(message)

                    #*******write email to file***************************
                    outfilename = str(ta_to.idta)
                    outfile = botslib.opendata(outfilename, 'wb')
                    generator = email.Generator.Generator(outfile, mangle_from_=False, maxheaderlen=78)
                    generator.flatten(message,unixfrom=False)
                    outfile.close()
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt)
            else:
                ta_to.update(statust=OK,filename=outfilename,confirmtype=confirmtype,confirmasked=confirmasked,charset=charset)
            finally:
                ta_from.update(statust=DONE)
        return

    def mime2file(self):
        ''' convert emails (mime-documents) to 'plain' files.
            from status FILEIN to FILEIN
            process emails:
            -   extract information (eg sender-address)
            -   generate MDN (if asked and OK from bots-configuration)
            -   process MDN
            -   save 'attachments' as files
            -   filter emails/attachments based on contenttype
            -   email-address should be know by bots (can be turned off)
        '''
        whitelist_multipart = ['multipart/mixed','multipart/digest','multipart/signed','multipart/report','message/rfc822','multipart/alternative']
        whitelist_major = ['text','application']
        blacklist_contenttype = ['text/html','text/enriched','text/rtf','text/richtext','application/postscript','text/vcard','text/css']
        def savemime(msg):
            ''' save contents of email as separate files.
                is a nested function.
                3x filtering:
                -   whitelist of multipart-contenttype
                -   whitelist of body-contentmajor
                -   blacklist of body-contentytpe
            '''
            nrmimesaved = 0     #count nr of valid 'attachments'
            contenttype     = msg.get_content_type()
            if msg.is_multipart():
                if contenttype in whitelist_multipart:
                    for part in msg.get_payload():
                        nrmimesaved += savemime(part)
            else:    #is not a multipart
                if msg.get_content_maintype() not in whitelist_major or contenttype in blacklist_contenttype:
                    return 0
                content = msg.get_payload(decode=True)
                if not content or content.isspace():
                    return 0
                charset = msg.get_content_charset('ascii')
                if self.userscript and hasattr(self.userscript,'accept_incoming_attachment'):
                    accept_attachment = botslib.runscript(self.userscript,self.scriptname,'accept_incoming_attachment',channeldict=self.channeldict,ta=ta_from,charset=charset,content=content,contenttype=contenttype)
                    if not accept_attachment:
                        return 0
                filesize = len(content)
                ta_file = ta_from.copyta(status=FILEIN)
                outfilename = str(ta_file.idta)
                outfile = botslib.opendata(outfilename, 'wb')
                outfile.write(content)
                outfile.close()
                nrmimesaved += 1
                ta_file.update(statust=OK,
                                contenttype=contenttype,
                                charset=charset,
                                filename=outfilename,
                                filesize=filesize)
            return nrmimesaved
        #*****************end of nested function savemime***************************
        @botslib.log_session
        def mdnreceive():
            tmp = msg.get_param('reporttype')
            if tmp is None or email.Utils.collapse_rfc2231_value(tmp)!='disposition-notification':    #invalid MDN
                raise botslib.CommunicationInError(_(u'Received email-MDN with errors.'))
            for part in msg.get_payload():
                if part.get_content_type()=='message/disposition-notification':
                    originalmessageid = part['original-message-id']
                    if originalmessageid is not None:
                        break
            else:   #invalid MDN: 'message/disposition-notification' not in email
                raise botslib.CommunicationInError(_(u'Received email-MDN with errors.'))
            botslib.changeq('''UPDATE ta
                               SET confirmed=%(confirmed)s, confirmidta=%(confirmidta)s
                               WHERE reference=%(reference)s
                               AND status=%(status)s
                               AND confirmasked=%(confirmasked)s
                               AND confirmtype=%(confirmtype)s
                               ''',
                                {'status':FILEOUT,'reference':originalmessageid,'confirmed':True,'confirmtype':'ask-email-MDN','confirmidta':ta_from.idta,'confirmasked':True})
            #for now no checking if processing was OK.....
            #performance: not good. Index should be on the reference.
        @botslib.log_session
        def mdnsend(ta_from):
            if not botslib.checkconfirmrules('send-email-MDN',idroute=self.idroute,idchannel=self.channeldict['idchannel'],
                                                            frompartner=frompartner,topartner=topartner):
                return 0 #do not send
            #make message
            message = email.Message.Message()
            message.add_header('From',tomail)
            dispositionnotificationto = email.Utils.parseaddr(msg['disposition-notification-to'])[1]
            message.add_header('To', dispositionnotificationto)
            message.add_header('Subject', 'Return Receipt (displayed) - '+subject)
            message.add_header('MIME-Version','1.0')
            message.add_header('Content-Type','multipart/report',reporttype='disposition-notification')
            #~ message.set_type('multipart/report')
            #~ message.set_param('reporttype','disposition-notification')

            #make human readable message
            humanmessage = email.Message.Message()
            humanmessage.add_header('Content-Type', 'text/plain')
            humanmessage.set_payload('This is an return receipt for the mail that you send to '+tomail)
            message.attach(humanmessage)

            #make machine readable message
            machinemessage = email.Message.Message()
            machinemessage.add_header('Content-Type', 'message/disposition-notification')
            machinemessage.add_header('Original-Message-ID', reference)
            nep = email.Message.Message()
            machinemessage.attach(nep)
            message.attach(machinemessage)

            #write email to file;
            ta_mdn = botslib.NewTransaction(status=MERGED)  #new transaction for group-file
            
            if botsglobal.ini.getboolean('acceptance','runacceptancetest',False):
                mdn_reference = '123message-ID email should be unique123'
                mdn_datetime = email.Utils.formatdate(timeval=time.mktime(time.strptime("2013-01-23 01:23:45", "%Y-%m-%d %H:%M:%S")),localtime=True)
            else:
                mdn_reference = email.Utils.make_msgid(str(ta_mdn.idta))    #we first have to get the mda-ta to make this reference
                mdn_datetime = email.Utils.formatdate(localtime=True)
            message.add_header('Date',mdn_datetime)
            message.add_header('Message-ID', mdn_reference)
            
            mdnfilename = str(ta_mdn.idta)
            mdnfile = botslib.opendata(mdnfilename, 'wb')
            generator = email.Generator.Generator(mdnfile, mangle_from_=False, maxheaderlen=78)
            generator.flatten(message,unixfrom=False)
            mdnfile.close()
            ta_mdn.update(statust=OK,
                            idroute=self.idroute,
                            filename=mdnfilename,
                            editype='email-confirmation',
                            frompartner=topartner,
                            topartner=frompartner,
                            frommail=tomail,
                            tomail=dispositionnotificationto,
                            reference=mdn_reference,
                            content='multipart/report',
                            fromchannel=self.channeldict['idchannel'],
                            charset='ascii',
                            parent=ta_from.idta)
            return ta_mdn.idta
        #*****************end of nested function dispositionnotification***************************
        #get received mails for channel
        for row in botslib.query('''SELECT idta,filename
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                    AND status=%(status)s
                                    AND statust=%(statust)s
                                    AND fromchannel=%(fromchannel)s
                                    ''',
                                    {'status':FILEIN,'statust':OK,'rootidta':self.rootidta,
                                    'fromchannel':self.channeldict['idchannel'],'idroute':self.idroute}):
            try:
                #default values for sending MDN; used to update ta if MDN is not asked
                confirmtype = ''
                confirmed = False
                confirmasked = False
                confirmidta = 0
                #read & parse email
                ta_from = botslib.OldTransaction(row['idta'])
                infile = botslib.opendata(row['filename'], 'rb')
                msg             = email.message_from_file(infile)   #read and parse mail
                infile.close()
                #******get information from email (sender, receiver etc)***********************************************************
                reference       = self.checkheaderforcharset(msg['message-id'])
                subject = self.checkheaderforcharset(msg['subject'])
                contenttype     = self.checkheaderforcharset(msg.get_content_type())
                #frompartner (incl autorization)
                frommail        = self.checkheaderforcharset(email.Utils.parseaddr(msg['from'])[1])
                frompartner = ''
                if not self.channeldict['starttls']:    #starttls in channeldict is: 'no check on "from:" email adress'
                    frompartner = self.mailaddress2idpartner(frommail)
                    if frompartner is None:
                        raise botslib.CommunicationInError(_(u'"From" emailaddress(es) %(email)s not authorised/unknown for channel "%(idchannel)s".'),
                                                            {'email':frommail,'idchannel':self.channeldict['idchannel']})
                #topartner, cc (incl autorization)
                list_to_address = [self.checkheaderforcharset(address) for name_not_used_variable,address in email.Utils.getaddresses(msg.get_all('to', []))] 
                list_cc_address = [self.checkheaderforcharset(address) for name_not_used_variable,address in email.Utils.getaddresses(msg.get_all('cc', []))] 
                cc_content      = ','.join([address for address in (list_to_address + list_cc_address)])
                topartner = ''  #initialise topartner
                tomail = ''     #initialise tomail
                if not self.channeldict['apop']:    #apop in channeldict is: 'no check on "to:" email adress'
                    for address in list_to_address:   #all tos-addresses are checked; only one needs to be authorised.
                        topartner =  self.mailaddress2idpartner(address)
                        tomail = address
                        if topartner is not None:   #if topartner found: break out of loop
                            break
                    else:   #if no valid topartner: generate error
                        raise botslib.CommunicationInError(_(u'"To" emailaddress(es) %(email)s not authorised/unknown for channel "%(idchannel)s".'),
                                                            {'email':list_to_address,'idchannel':self.channeldict['idchannel']})
 
                #update transaction of mail with information found in mail
                ta_from.update(frommail=frommail,   #why save now not later: when saving the attachments need the amil-header-info to be in ta (copyta)
                                tomail=tomail,
                                reference=reference,
                                contenttype=contenttype,
                                frompartner=frompartner,
                                topartner=topartner,
                                cc = cc_content,
                                rsrv1 = subject)
                if contenttype == 'multipart/report':   #process received MDN confirmation
                    mdnreceive()
                else:
                    if msg.has_key('disposition-notification-To'):  #sender requests a MDN
                        confirmidta = mdnsend(ta_from)
                        if confirmidta:
                            confirmtype = 'send-email-MDN'
                            confirmed = True
                            confirmasked = True
                    nrmimesaved = savemime(msg)
                    if not nrmimesaved:
                        raise botslib.CommunicationInError (_(u'No valid attachment in received email'))
            except:
                txt = botslib.txtexc()
                ta_from.update(statust=ERROR,errortext=txt)
                ta_from.deletechildren()
            else:
                ta_from.update(statust=DONE,confirmtype=confirmtype,confirmed=confirmed,confirmasked=confirmasked,confirmidta=confirmidta)
        return

    @staticmethod
    def checkheaderforcharset(org_header):
        ''' correct handling of charset for email headers that are saved in database.
        ''' 
        header,encoding = email.header.decode_header(org_header)[0]    #for subjects with non-ascii content special notation exists in MIME-standard
        try:
            if encoding is not None:
                return header.decode(encoding)                       #decode (to unicode)
            #test if valid; use case: spam... need to test because database-storage will give errors otherwise. 
            header.encode('utf8')
            return header
        except:
            raise botslib.CommunicationInError(_(u'Email header invalid - probably issues with characterset.'))
                        
    def mailaddress2idpartner(self,mailaddress):
        ''' lookup email address to see if know in configuration. '''
        #first check in chanpar email-addresses for this channel
        for row in botslib.query(u'''SELECT chanpar.idpartner_id as idpartner
                                    FROM chanpar,channel,partner
                                    WHERE chanpar.idchannel_id=channel.idchannel
                                    AND chanpar.idpartner_id=partner.idpartner
                                    AND partner.active=%(active)s
                                    AND chanpar.idchannel_id=%(idchannel)s
                                    AND LOWER(chanpar.mail)=%(mail)s''',
                                    {'active':True,'idchannel':self.channeldict['idchannel'],'mail':mailaddress.lower()}):
            return row['idpartner']
        #if not found, check in partner-tabel (is less specific)
        for row in botslib.query(u'''SELECT idpartner
                                    FROM partner
                                    WHERE active=%(active)s
                                    AND LOWER(mail)=%(mail)s''',
                                    {'active':True,'mail':mailaddress.lower()}):
            return row['idpartner']
        return None     #indicate email address is unknown


    def idpartner2mailaddress(self,idpartner):
        for row in botslib.query(u'''SELECT chanpar.mail as mail,chanpar.cc as cc
                                    FROM chanpar,channel,partner
                                    WHERE chanpar.idchannel_id=channel.idchannel
                                    AND chanpar.idpartner_id=partner.idpartner
                                    AND partner.active=%(active)s
                                    AND chanpar.idchannel_id=%(idchannel)s
                                    AND chanpar.idpartner_id=%(idpartner)s''',
                                    {'active':True,'idchannel':self.channeldict['idchannel'],'idpartner':idpartner}):
            if row['mail']:
                return row['mail'],row['cc']
        for row in botslib.query(u'''SELECT mail,cc
                                    FROM partner
                                    WHERE active=%(active)s
                                    AND idpartner=%(idpartner)s''',
                                    {'active':True,'idpartner':idpartner}):
            if row['mail']:
                return row['mail'],row['cc']
        raise botslib.CommunicationOutError(_(u'No mail-address for partner "%(partner)s" (channel "%(idchannel)s").'),
                                                {'partner':idpartner,'idchannel':self.channeldict['idchannel']})

    def connect(self):
        pass

    def disconnect(self):
        pass

    @staticmethod
    def convertcodecformime(codec_in):
        convertdict = {
            'ascii' : 'us-ascii',
            'unoa' : 'us-ascii',
            'unob' : 'us-ascii',
            'unoc' : 'iso-8859-1',
            }
        codec_in = codec_in.lower().replace('_','-')
        return convertdict.get(codec_in,codec_in)


    def filename_formatter(self,filename_mask,ta):
        ''' Output filename generation from "template" filename configured in the channel
            Basically python's string.Formatter is used; see http://docs.python.org/library/string.html
            As in string.Formatter, substitution values are surrounded by braces; format specifiers can be used.
            Any ta value can be used
              eg. {botskey}, {alt}, {editype}, {messagetype}, {topartner}
            Next to the value in ta you can use:
            -   * : an unique number (per outchannel) using an asterisk
            -   {datetime}  use datetime with a valid strftime format: 
                eg. {datetime:%Y%m%d}, {datetime:%H%M%S}
            -   {infile} use the original incoming filename; use name and extension, or either part separately:
                eg. {infile}, {infile:name}, {infile:ext}
            -   {overwrite}  if file wit hfielname exists: overwrite it (instead of appending)
            
            Exampels of usage:
                {botskey}_*.idoc        use incoming order number, add unique number, use extension '.idoc'
                *_{infile}              passthrough incoming filename & extension, prepend with unique number
                {infile:name}_*.txt     passthrough incoming filename, add unique number but change extension to .txt
                {editype}-{messagetype}-{datetime:%Y%m%d}-*.{infile:ext}
                                        use editype, messagetype, date and unique number with extension from the incoming file
                {topartner}/{messagetype}/*.edi
                                        Usage of subdirectories in the filename, they must already exist. In the example:
                                        sort into folders by partner and messagetype.
                        
            Note1: {botskey} can only be used if merge is False for that messagetype
        '''
        class infilestr(str):
            ''' class for the infile-string that handles the specific format-options'''
            def __format__(self, format_spec):
                if not format_spec:
                    return str(self)
                name,ext = os.path.splitext(str(self))
                if format_spec == 'ext':
                    if ext.startswith('.'):
                        ext = ext[1:]
                    return ext 
                if format_spec == 'name':
                    return name 
                raise botslib.CommunicationOutError(_(u'Error in format of "{filename}": unknown format: "%(format)s".'),
                                                    {'format':format_spec})
        unique = str(botslib.unique(self.channeldict['idchannel'])) #create unique part for attachment-filename
        tofilename = filename_mask.replace('*',unique)           #filename_mask is filename in channel where '*' is replaced by idta
        if '{' in tofilename and sys.version_info[1] != 5:    #only for python 2.6/7
            ta.synall()
            if '{infile' in tofilename:
                ta_list = botslib.trace_origin(ta=ta,where={'status':EXTERNIN})
                if ta_list:
                    infilename = infilestr(os.path.basename(ta_list[-1].filename))
                else:
                    infilename = ''
            else:
                infilename = ''
            try:
                if botsglobal.ini.getboolean('acceptance','runacceptancetest',False):
                    datetime_object = datetime.datetime.strptime("2013-01-23 01:23:45", "%Y-%m-%d %H:%M:%S")
                else:
                    datetime_object = datetime.datetime.now()
                tofilename = tofilename.format(infile=infilename,datetime=datetime_object,**ta.__dict__)
            except:
                txt = botslib.txtexc()
                raise botslib.CommunicationOutError(_(u'Error in formatting outgoing filename "%(filename)s". Error: "%(error)s".'),
                                                        {'filename':tofilename,'error':txt})
        if self.userscript and hasattr(self.userscript,'filename'):
            return botslib.runscript(self.userscript,self.scriptname,'filename',channeldict=self.channeldict,filename=tofilename,ta=ta)
        else:
            return tofilename


class file(_comsession):
    def connect(self):
        if self.channeldict['lockname']:        #directory locking: create lock-file. If the lockfile is already present an exception is raised.
            self.lockname = botslib.join(self.channeldict['path'],self.channeldict['lockname'])
            lock = os.open(self.lockname,os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(lock)

    @botslib.log_session
    def incommunicate(self):
        ''' gets files from filesystem.
        '''
        frompath = botslib.join(self.channeldict['path'],self.channeldict['filename'])
        filelist = [filename for filename in glob.iglob(frompath) if os.path.isfile(filename)]
        filelist.sort()
        startdatetime = datetime.datetime.now()
        for fromfilename in filelist:
            try:
                ta_from = botslib.NewTransaction(filename=fromfilename,
                                                status=EXTERNIN,
                                                fromchannel=self.channeldict['idchannel'],
                                                idroute=self.idroute)
                ta_to =   ta_from.copyta(status=FILEIN)
                #open fromfile, syslock if indicated
                fromfile = open(fromfilename,'rb')
                filesize = os.fstat(fromfile.fileno()).st_size
                if self.channeldict['syslock']:
                    if os.name == 'nt':
                        msvcrt.locking(fromfile.fileno(), msvcrt.LK_LOCK, 0x0fffffff)
                    elif os.name == 'posix':
                        fcntl.lockf(fromfile.fileno(), fcntl.LOCK_SH|fcntl.LOCK_NB)
                    else:
                        raise botslib.LockedFileError(_(u'Can not do a systemlock on this platform'))
                #open tofile
                tofilename = str(ta_to.idta)
                tofile = botslib.opendata(tofilename, 'wb')
                #copy
                shutil.copyfileobj(fromfile,tofile,1048576)
                tofile.close()
                fromfile.close()
            except:
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='file-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
            else:
                ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                ta_from.update(statust=DONE)
                if self.channeldict['remove']:
                    os.remove(fromfilename)
            finally:
                if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                    break

    @botslib.log_session
    def outcommunicate(self):
        ''' does output of files to filesystem. To be used via send-dispatcher.
            Output is either:
            1.  1 outputfile, messages are appended; filename is a fixed name
            2.  to directory; new file for each db-ta; if file exits: overwrite. File has to have a unique name.
        '''
        #check if output dir exists, else create it.
        outputdir = botslib.join(self.channeldict['path'])
        botslib.dirshouldbethere(outputdir)
        #get right filename_mask & determine if fixed name (append) or files with unique names
        filename_mask = self.channeldict['filename'] if self.channeldict['filename'] else '*'
        if '{overwrite}' in filename_mask:
            filename_mask = filename_mask.replace('{overwrite}','')
            mode = 'wb'
        else:
            mode = 'ab'
        #select the db-ta's for this channel
        for row in botslib.query(u'''SELECT idta,filename,numberofresends
                                       FROM ta
                                      WHERE idta>%(rootidta)s
                                        AND status=%(status)s
                                        AND statust=%(statust)s
                                        AND tochannel=%(tochannel)s
                                        ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,
                                    'status':FILEOUT,'statust':OK}):
            try:    #for each db-ta:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to =   ta_from.copyta(status=EXTERNOUT)
                #open tofile, incl syslock if indicated
                tofilename = self.filename_formatter(filename_mask,ta_from)
                tofilename = botslib.join(outputdir,tofilename)
                tofile = open(tofilename, mode)
                if self.channeldict['syslock']:
                    if os.name == 'nt':
                        msvcrt.locking(tofile.fileno(), msvcrt.LK_LOCK, 0x0fffffff)
                    elif os.name == 'posix':
                        fcntl.lockf(tofile.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
                    else:
                        raise botslib.LockedFileError(_(u'Can not do a systemlock on this platform'))
                #open fromfile
                fromfile = botslib.opendata(row['filename'], 'rb')
                #copy
                shutil.copyfileobj(fromfile,tofile,1048576)
                fromfile.close()
                tofile.close()
                #~ raise Exception('test')
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename=tofilename,numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)

    def disconnect(self):
        #delete directory-lockfile
        if self.channeldict['lockname']:
            os.remove(self.lockname)


class pop3(_comsession):
    def connect(self):
        import poplib
        self.session = poplib.POP3(host=self.channeldict['host'],port=int(self.channeldict['port']))
        self.session.set_debuglevel(botsglobal.ini.getint('settings','pop3debug',0))    #if used, gives information about session (on screen), for debugging pop3
        self.session.user(self.channeldict['username'])
        self.session.pass_(self.channeldict['secret'])

    @botslib.log_session
    def incommunicate(self):
        ''' Fetch messages from Pop3-mailbox.
            A bad connection is tricky, because mails are actually only deleted on the server when QUIT is successful.
            A solution would be to connect, fetch, delete and quit for each mail, but this might introduce other problems.
            So: keep a list of idta received OK.
            If QUIT is not successful than delete these ta's
        '''
        self.listoftamarkedfordelete = []
        maillist = self.session.list()[1]     #get list of messages #alt: (response, messagelist, octets) = popsession.list()     #get list of messages
        startdatetime = datetime.datetime.now()
        for mail in maillist:
            try:
                ta_from = botslib.NewTransaction(filename='pop3://'+self.channeldict['username']+'@'+self.channeldict['host'],
                                                    status=EXTERNIN,
                                                    fromchannel=self.channeldict['idchannel'],idroute=self.idroute)
                ta_to =   ta_from.copyta(status=FILEIN)
                tofilename = str(ta_to.idta)
                mailid = int(mail.split()[0])	#first 'word' is the message number/ID
                maillines = self.session.retr(mailid)[1]        #alt: (header, messagelines, octets) = popsession.retr(messageID)
                tofile = botslib.opendata(tofilename, 'wb')
                content = os.linesep.join(maillines)
                filesize = len(content)
                tofile.write(content)
                tofile.close()
                if self.channeldict['remove']:      #on server side mail is marked to be deleted. The pop3-server will actually delete the file if the QUIT commnd is receieved!
                    self.session.dele(mailid)
                    #add idta's of received mail in a list. If connection is not OK, QUIT command to POP3 server will not work. deleted mail will still be on server.
                    self.listoftamarkedfordelete += [ta_from.idta,ta_to.idta]

            except:         #something went wrong for this mail.
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='pop3-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
                #test connection. if connection is not OK stop fetching mails.
                try:
                    self.session.noop()
                except:
                    self.session = None     #indicate session is not valid anymore
                    break
            else:
                ta_to.update(statust=OK,filename=tofilename,filesize=filesize)
                ta_from.update(statust=DONE)
            finally:
                if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                    break

    def disconnect(self):
        try:
            if not self.session:
                raise Exception(_(u'Pop3 connection not OK'))
            resp = self.session.quit()     #pop3 server will now actually delete the mails
            if resp[:1] != '+':
                raise Exception(_(u'QUIT command to POP3 server failed'))
        except Exception:   #connection is gone. Delete everything that is received to avoid double receiving.
            botslib.ErrorProcess(functionname='pop3-incommunicate',errortext='Could not fetch emails via POP3; probably communication problems',channeldict=self.channeldict)
            for idta in self.listoftamarkedfordelete:
                ta = botslib.OldTransaction(idta)
                ta.delete()

    @botslib.log_session
    def postcommunicate(self):
        self.mime2file()

class pop3s(pop3):
    def connect(self):
        import poplib
        if self.userscript and hasattr(self.userscript,'keyfile'):
            keyfile, certfile = botslib.runscript(self.userscript,self.scriptname,'keyfile',channeldict=self.channeldict)
        elif self.channeldict['keyfile']:
            keyfile = self.channeldict['keyfile']
            certfile = self.channeldict['certfile']
        else:
            keyfile = certfile = None
        self.session = poplib.POP3_SSL(host=self.channeldict['host'],port=int(self.channeldict['port']),keyfile=keyfile,certfile=certfile)
        self.session.set_debuglevel(botsglobal.ini.getint('settings','pop3debug',0))    #if used, gives information about session (on screen), for debugging pop3
        self.session.user(self.channeldict['username'])
        self.session.pass_(self.channeldict['secret'])

class pop3apop(pop3):
    def connect(self):
        import poplib
        self.session = poplib.POP3(host=self.channeldict['host'],port=int(self.channeldict['port']))
        self.session.set_debuglevel(botsglobal.ini.getint('settings','pop3debug',0))    #if used, gives information about session (on screen), for debugging pop3
        self.session.apop(self.channeldict['username'],self.channeldict['secret'])    #python handles apop password encryption


class imap4(_comsession):
    ''' Fetch email from IMAP server.
    '''
    def connect(self):
        import imaplib
        imaplib.Debug = botsglobal.ini.getint('settings','imap4debug',0)    #if used, gives information about session (on screen), for debugging imap4
        self.session = imaplib.IMAP4(host=self.channeldict['host'],port=int(self.channeldict['port']))
        self.session.login(self.channeldict['username'],self.channeldict['secret'])

    @botslib.log_session
    def incommunicate(self):
        ''' Fetch messages from imap4-mailbox.
        '''
        # path may contain a mailbox name, otherwise use INBOX
        if self.channeldict['path']:
            mailbox_name = self.channeldict['path']
        else:
            mailbox_name = 'INBOX'

        response, data = self.session.select(mailbox_name)
        if response != 'OK': # eg. mailbox does not exist
            raise botslib.CommunicationError(mailbox_name + ': ' + data[0])

        # Get the message UIDs that should be read
        response, data = self.session.uid('search', None, '(UNDELETED)')
        if response != 'OK': # have never seen this happen, but just in case!
            raise botslib.CommunicationError(mailbox_name + ': ' + data[0])

        maillist = data[0].split()
        startdatetime = datetime.datetime.now()
        for mail in maillist:
            try:
                ta_from = botslib.NewTransaction(filename='imap4://'+self.channeldict['username']+'@'+self.channeldict['host'],
                                                    status=EXTERNIN,
                                                    fromchannel=self.channeldict['idchannel'],idroute=self.idroute)
                ta_to =   ta_from.copyta(status=FILEIN)
                filename = str(ta_to.idta)
                # Get the message (header and body)
                response, msg_data = self.session.uid('fetch',mail, '(RFC822)')
                filehandler = botslib.opendata(filename, 'wb')
                filesize = len(msg_data[0][1])
                filehandler.write(msg_data[0][1])
                filehandler.close()
                # Flag message for deletion AND expunge. Direct expunge has advantages for bad (internet)connections.
            except:
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='imap4-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
            else:
                ta_to.update(statust=OK,filename=filename,filesize=filesize)
                ta_from.update(statust=DONE)
                if self.channeldict['remove']:
                    self.session.uid('store',mail, '+FLAGS', r'(\Deleted)')
                    self.session.expunge()
            finally:
                if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                    break

        self.session.close()        #Close currently selected mailbox. This is the recommended command before 'LOGOUT'.

    @botslib.log_session
    def postcommunicate(self):
        self.mime2file()

    def disconnect(self):
        self.session.logout()

class imap4s(imap4):
    def connect(self):
        import imaplib
        if self.userscript and hasattr(self.userscript,'keyfile'):
            keyfile, certfile = botslib.runscript(self.userscript,self.scriptname,'keyfile',channeldict=self.channeldict)
        elif self.channeldict['keyfile']:
            keyfile = self.channeldict['keyfile']
            certfile = self.channeldict['certfile']
        else:
            keyfile = certfile = None
        imaplib.Debug = botsglobal.ini.getint('settings','imap4debug',0)    #if used, gives information about session (on screen), for debugging imap4
        self.session = imaplib.IMAP4_SSL(host=self.channeldict['host'],port=int(self.channeldict['port']),keyfile=keyfile,certfile=certfile)
        self.session.login(self.channeldict['username'],self.channeldict['secret'])


class smtp(_comsession):
    @botslib.log_session
    def precommunicate(self):
        self.file2mime()

    def connect(self):
        self.session = smtplib.SMTP(host=self.channeldict['host'],port=int(self.channeldict['port'])) #make connection
        self.session.set_debuglevel(botsglobal.ini.getint('settings','smtpdebug',0))    #if used, gives information about session (on screen), for debugging smtp
        self.login()

    def login(self):
        if self.channeldict['username'] and self.channeldict['secret']:
            try:
                #error in python 2.6.4....user and password can not be unicode
                self.session.login(str(self.channeldict['username']),str(self.channeldict['secret']))
            except smtplib.SMTPAuthenticationError:
                raise botslib.CommunicationOutError(_(u'SMTP server did not accept user/password combination.'))
            except:
                txt = botslib.txtexc()
                raise botslib.CommunicationOutError(_(u'SMTP login failed. Error:\n%(txt)s'),{'txt':txt})

    @botslib.log_session
    def outcommunicate(self):
        ''' does smtp-session.
            SMTP does not allow rollback. So if the sending of a mail fails, other mails may have been send.
        '''
        #send messages
        for row in botslib.query(u'''SELECT idta,filename,frommail,tomail,cc,numberofresends
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                    AND status=%(status)s
                                    AND statust=%(statust)s
                                    AND tochannel=%(tochannel)s
                                    ''',
                                    {'status':FILEOUT,'statust':OK,'rootidta':self.rootidta,
                                    'tochannel':self.channeldict['idchannel']}):
            try:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to = ta_from.copyta(status=EXTERNOUT)
                addresslist = row['cc'].split(',')
                addresslist.append(row['tomail'])
                addresslist = [x.strip() for x in addresslist if x.strip()]
                sendfile = botslib.opendata(row['filename'], 'rb')
                msg = sendfile.read()
                sendfile.close()
                self.session.sendmail(row['frommail'], addresslist, msg)
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,filename='smtp://'+self.channeldict['username']+'@'+self.channeldict['host'],numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename='smtp://'+self.channeldict['username']+'@'+self.channeldict['host'],numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)

    def disconnect(self):
        try:    #Google gives/gave error closing connection. Not a real problem.
            self.session.quit()
        except socket.sslerror:     #for a starttls connection
            self.session.close()
        except:
            pass

class smtps(smtp):
    def connect(self):
        if self.userscript and hasattr(self.userscript,'keyfile'):
            keyfile, certfile = botslib.runscript(self.userscript,self.scriptname,'keyfile',channeldict=self.channeldict)
        elif self.channeldict['keyfile']:
            keyfile = self.channeldict['keyfile']
            certfile = self.channeldict['certfile']
        else:
            keyfile = certfile = None
        if hasattr(smtplib,'SMTP_SSL'):
            self.session = smtplib.SMTP_SSL(host=self.channeldict['host'],port=int(self.channeldict['port']),keyfile=keyfile,certfile=certfile) #make connection
        else:   #smtp_ssl not in standard lib for python<=2.5; if not, use 'own' smtps module.
            import bots.smtpssllib as smtpssllib
            self.session = smtpssllib.SMTP_SSL(host=self.channeldict['host'],port=int(self.channeldict['port']),keyfile=keyfile,certfile=certfile) #make connection
        self.session.set_debuglevel(botsglobal.ini.getint('settings','smtpdebug',0))    #if used, gives information about session (on screen), for debugging smtp
        self.login()

class smtpstarttls(smtp):
    def connect(self):
        if self.userscript and hasattr(self.userscript,'keyfile'):
            keyfile, certfile = botslib.runscript(self.userscript,self.scriptname,'keyfile',channeldict=self.channeldict)
        elif self.channeldict['keyfile']:
            keyfile = self.channeldict['keyfile']
            certfile = self.channeldict['certfile']
        else:
            keyfile = certfile = None
        self.session = smtplib.SMTP(host=self.channeldict['host'],port=int(self.channeldict['port'])) #make connection
        self.session.set_debuglevel(botsglobal.ini.getint('settings','smtpdebug',0))    #if used, gives information about session (on screen), for debugging smtp
        self.session.ehlo()
        self.session.starttls(keyfile=keyfile,certfile=certfile)
        self.session.ehlo()
        self.login()


class mimefile(file):
    @botslib.log_session
    def postcommunicate(self):
        self.mime2file()
    @botslib.log_session
    def precommunicate(self):
        self.file2mime()

class ftp(_comsession):
    def connect(self):
        botslib.settimeout(botsglobal.ini.getint('settings','ftptimeout',10))
        self.session = ftplib.FTP()
        self.session.set_debuglevel(botsglobal.ini.getint('settings','ftpdebug',0))   #set debug level (0=no, 1=medium, 2=full debug)
        self.session.set_pasv(not self.channeldict['ftpactive']) #active or passive ftp
        self.session.connect(host=self.channeldict['host'],port=int(self.channeldict['port']))
        self.session.login(user=self.channeldict['username'],passwd=self.channeldict['secret'],acct=self.channeldict['ftpaccount'])
        self.set_cwd()

    def set_cwd(self):
        self.dirpath = self.session.pwd()
        if self.channeldict['path']:
            self.dirpath = posixpath.normpath(posixpath.join(self.dirpath,self.channeldict['path']))
            try:
                self.session.cwd(self.dirpath)           #set right path on ftp-server
            except:
                self.session.mkd(self.dirpath)           #set right path on ftp-server; no nested directories
                self.session.cwd(self.dirpath)           #set right path on ftp-server

    @botslib.log_session
    def incommunicate(self):
        ''' do ftp: receive files. To be used via receive-dispatcher.
            each to be imported file is transaction.
            each imported file is transaction.
        '''
        startdatetime = datetime.datetime.now()
        files = []
        try:            #some ftp servers give errors when directory is empty; catch these errors here
            files = self.session.nlst()
        except (ftplib.error_perm,ftplib.error_temp),msg:
            if str(msg)[:3] not in ['550','450']:
                raise

        lijst = fnmatch.filter(files,self.channeldict['filename'])
        for fromfilename in lijst:  #fetch messages from ftp-server.
            try:
                ta_from = botslib.NewTransaction(filename='ftp:/'+posixpath.join(self.dirpath,fromfilename),
                                                    status=EXTERNIN,
                                                    fromchannel=self.channeldict['idchannel'],
                                                    idroute=self.idroute)
                ta_to =   ta_from.copyta(status=FILEIN)
                tofilename = str(ta_to.idta)
                tofile = botslib.opendata(tofilename, 'wb')
                try:
                    if self.channeldict['ftpbinary']:
                        self.session.retrbinary("RETR " + fromfilename, tofile.write)
                    else:
                        self.session.retrlines("RETR " + fromfilename, lambda s, w=tofile.write: w(s+"\n"))
                except ftplib.error_perm, msg:
                    if str(msg)[:3] in ['550',]:     #we are trying to download a directory...
                        raise botslib.BotsError(u'To be catched')
                    else:
                        raise
                tofile.close()
                filesize = os.path.getsize(botslib.abspathdata(tofilename))
                if not filesize:
                    raise botslib.BotsError(u'To be catched; directory (or empty file)')
            except botslib.BotsError:   #directory or empty file; handle exception but generate no error.
                ta_from.delete()
                ta_to.delete()
            except:
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='ftp-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
            else:
                ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                ta_from.update(statust=DONE)
                if self.channeldict['remove']:
                    self.session.delete(fromfilename)
            finally:
                if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                    break

    @botslib.log_session
    def outcommunicate(self):
        ''' do ftp: send files. To be used via receive-dispatcher.
            each to be send file is transaction.
            each send file is transaction.
            NB: ftp command APPE should be supported by server
        '''
        #get right filename_mask & determine if fixed name (append) or files with unique names
        filename_mask = self.channeldict['filename'] if self.channeldict['filename'] else '*'
        if '{overwrite}' in filename_mask:
            filename_mask = filename_mask.replace('{overwrite}','')
            mode = 'STOR '
        else:
            mode = 'APPE '
        for row in botslib.query('''SELECT idta,filename,numberofresends
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                      AND status=%(status)s
                                      AND statust=%(statust)s
                                      AND tochannel=%(tochannel)s
                                        ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,
                                    'status':FILEOUT,'statust':OK}):
            try:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to = ta_from.copyta(status=EXTERNOUT)
                tofilename = self.filename_formatter(filename_mask,ta_from)
                if self.channeldict['ftpbinary']:
                    fromfile = botslib.opendata(row['filename'], 'rb')
                    self.session.storbinary(mode + tofilename, fromfile)
                else:
                    fromfile = botslib.opendata(row['filename'], 'r')
                    self.session.storlines(mode + tofilename, fromfile)
                fromfile.close()
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,filename='ftp:/'+posixpath.join(self.dirpath,tofilename),numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename='ftp:/'+posixpath.join(self.dirpath,tofilename),numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)

    def disconnect(self):
        try:
            self.session.quit()
        except:
            self.session.close()
        botslib.settimeout(botsglobal.ini.getint('settings','globaltimeout',10))

class ftps(ftp):
    ''' explicit ftps as defined in RFC 2228 and RFC 4217.
        standard port to connect to is as in normal FTP (port 21)
        ftps is supported by python >= 2.7
    '''
    def connect(self):
        if not hasattr(ftplib,'FTP_TLS'):
            raise botslib.CommunicationError(_(u'ftps is not supported by your python version, use >=2.7'))
        if self.userscript and hasattr(self.userscript,'keyfile'):
            keyfile, certfile = botslib.runscript(self.userscript,self.scriptname,'keyfile',channeldict=self.channeldict)
        elif self.channeldict['keyfile']:
            keyfile = self.channeldict['keyfile']
            certfile = self.channeldict['certfile']
        else:
            keyfile = certfile = None
        botslib.settimeout(botsglobal.ini.getint('settings','ftptimeout',10))
        self.session = ftplib.FTP_TLS(keyfile=keyfile,certfile=certfile)
        self.session.set_debuglevel(botsglobal.ini.getint('settings','ftpdebug',0))   #set debug level (0=no, 1=medium, 2=full debug)
        self.session.set_pasv(not self.channeldict['ftpactive']) #active or passive ftp
        self.session.connect(host=self.channeldict['host'],port=int(self.channeldict['port']))
        self.session.auth()
        self.session.login(user=self.channeldict['username'],passwd=self.channeldict['secret'],acct=self.channeldict['ftpaccount'])
        self.session.prot_p()
        self.set_cwd()


#sub classing of ftplib for ftpis
if hasattr(ftplib,'FTP_TLS'):
    class Ftp_tls_implicit(ftplib.FTP_TLS):
        ''' FTPS implicit is not directly supported by python; python>=2.7 supports only ftps explicit.
            So class ftplib.FTP_TLS is sub-classed here, with the needed modifications.
            (code is nicked from ftplib.ftp v. 2.7; additions/changes are indicated)
            '''
        def connect(self, host='', port=0, timeout=-999):
            #added hje 20110713: directly use SSL in FTPIS
            #end added
            if host != '':
                self.host = host
            if port > 0:
                self.port = port
            if timeout != -999:
                self.timeout = timeout
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
            self.af = self.sock.family
            #added hje 20110713: directly use SSL in FTPIS
            self.sock = ssl.wrap_socket(self.sock, self.keyfile, self.certfile,ssl_version=self.ssl_version)
            #end added
            self.file = self.sock.makefile('rb')
            self.welcome = self.getresp()
            return self.welcome
        def prot_p(self):
            #Inovis FTPIS gives errors on 'PBSZ 0' and 'PROT P', vsftp does not work without these commands.
            #These errors are just catched, nothing is done with them.
            try:
                self.voidcmd('PBSZ 0')
            except ftplib.error_perm:
                pass
            try:
                resp = self.voidcmd('PROT P')
            except ftplib.error_perm:
                resp = None
            self._prot_p = True
            return resp


class ftpis(ftp):
    ''' FTPS implicit; is not defined in a RFC.
        standard port to connect is port 990.
        FTPS implicit is not supported by python.
        python>=2.7 supports ftps explicit.
        So used is the sub-class Ftp_tls_implicit.
        Tested with Inovis and VSFTPd.
        Python library FTP_TLS uses ssl_version = ssl.PROTOCOL_TLSv1
        Inovis seems to need PROTOCOL_SSLv3
        This is 'solved' by using 'parameters' in the channel.
        ~ ssl.PROTOCOL_SSLv2  = 0
        ~ ssl.PROTOCOL_SSLv3  = 1
        ~ ssl.PROTOCOL_SSLv23 = 2
        ~ ssl.PROTOCOL_TLSv1  = 3
    '''
    def connect(self):
        if not hasattr(ftplib,'FTP_TLS'):
            raise botslib.CommunicationError(_(u'ftpis is not supported by your python version, use >=2.7'))
        if self.userscript and hasattr(self.userscript,'keyfile'):
            keyfile, certfile = botslib.runscript(self.userscript,self.scriptname,'keyfile',channeldict=self.channeldict)
        elif self.channeldict['keyfile']:
            keyfile = self.channeldict['keyfile']
            certfile = self.channeldict['certfile']
        else:
            keyfile = certfile = None
        botslib.settimeout(botsglobal.ini.getint('settings','ftptimeout',10))
        self.session = Ftp_tls_implicit(keyfile=keyfile,certfile=certfile)
        if self.channeldict['parameters']:
            self.session.ssl_version = int(self.channeldict['parameters'])
        self.session.set_debuglevel(botsglobal.ini.getint('settings','ftpdebug',0))   #set debug level (0=no, 1=medium, 2=full debug)
        self.session.set_pasv(not self.channeldict['ftpactive']) #active or passive ftp
        self.session.connect(host=self.channeldict['host'],port=int(self.channeldict['port']))
        #~ self.session.auth()
        self.session.login(user=self.channeldict['username'],passwd=self.channeldict['secret'],acct=self.channeldict['ftpaccount'])
        self.session.prot_p()
        self.set_cwd()


class sftp(_comsession):
    ''' SFTP: SSH File Transfer Protocol (SFTP is not FTP run over SSH, SFTP is not Simple File Transfer Protocol)
        standard port to connect to is port 22.
        requires paramiko and pycrypto to be installed
        based on class ftp and ftps above with code from demo_sftp.py which is included with paramiko
        Mike Griffin 16/10/2010
        Henk-jan ebbers 20110802: when testing I found that the transport also needs to be closed. So changed transport ->self.transport, and close this in disconnect
        henk-jan ebbers 20111019: disabled the host_key part for now (but is very interesting). Is not tested; keys should be integrated in bots also for other protocols.
        henk-jan ebbers 20120522: hostkey and privatekey can now be handled in user exit.
    '''
    def connect(self):
        # check dependencies
        try:
            import paramiko
        except:
            txt = botslib.txtexc()
            raise ImportError(_(u'Dependency failure: communicationtype "sftp" requires python library "paramiko". Error:\n%(txt)s'),{'txt':txt})
        try:
            from Crypto import Cipher
        except:
            txt = botslib.txtexc()
            raise ImportError(_(u'Dependency failure: communicationtype "sftp" requires python library "pycrypto". Error:\n%(txt)s'),{'txt':txt})
        # setup logging if required
        ftpdebug = botsglobal.ini.getint('settings','ftpdebug',0)
        if ftpdebug > 0:
            log_file = botslib.join(botsglobal.ini.get('directories','logging'),'sftp.log')
            # Convert ftpdebug to paramiko logging level (1=20=info, 2=10=debug)
            paramiko.util.log_to_file(log_file, 30-(ftpdebug*10))

        # Get hostname and port to use
        hostname = self.channeldict['host']
        try:
            port = int(self.channeldict['port'])
        except:
            port = 22 # default port for sftp

        if self.userscript and hasattr(self.userscript,'hostkey'):
            hostkey = botslib.runscript(self.userscript,self.scriptname,'hostkey',channeldict=self.channeldict)
        else:
            hostkey = None
        if self.userscript and hasattr(self.userscript,'privatekey'):
            privatekeyfile,pkeytype,pkeypassword = botslib.runscript(self.userscript,self.scriptname,'privatekey',channeldict=self.channeldict)
            if pkeytype == 'RSA':
                pkey = paramiko.RSAKey.from_private_key_file(filename=privatekeyfile,password=pkeypassword)
            else:
                pkey = paramiko.DSSKey.from_private_key_file(filename=privatekeyfile,password=pkeypassword)
        else:
            pkey = None

        if self.channeldict['secret']:  #if password is empty string: use None. Else error can occur.
            secret = self.channeldict['secret']
        else:
            secret = None
        # now, connect and use paramiko Transport to negotiate SSH2 across the connection
        self.transport = paramiko.Transport((hostname,port))
        self.transport.connect(username=self.channeldict['username'],password=secret,hostkey=hostkey,pkey=pkey)
        self.session = paramiko.SFTPClient.from_transport(self.transport)
        channel = self.session.get_channel()
        channel.settimeout(botsglobal.ini.getint('settings','ftptimeout',10))
        self.set_cwd()

    def set_cwd(self):
        self.session.chdir('.') # getcwd does not work without this chdir first!
        self.dirpath = self.session.getcwd()
        if self.channeldict['path']:
            self.dirpath = posixpath.normpath(posixpath.join(self.dirpath,self.channeldict['path']))
            try:
                self.session.chdir(self.dirpath)
            except:
                self.session.mkdir(self.dirpath)
                self.session.chdir(self.dirpath)

    def disconnect(self):
        self.session.close()
        self.transport.close()

    @botslib.log_session
    def incommunicate(self):
        ''' do ftp: receive files. To be used via receive-dispatcher.
            each to be imported file is transaction.
            each imported file is transaction.
        '''
        startdatetime = datetime.datetime.now()
        files = self.session.listdir('.')
        lijst = fnmatch.filter(files,self.channeldict['filename'])
        for fromfilename in lijst:  #fetch messages from sftp-server.
            try:
                ta_from = botslib.NewTransaction(filename='sftp:/'+posixpath.join(self.dirpath,fromfilename),
                                                    status=EXTERNIN,
                                                    fromchannel=self.channeldict['idchannel'],
                                                    idroute=self.idroute)
                ta_to =   ta_from.copyta(status=FILEIN)
                tofilename = str(ta_to.idta)
                fromfile = self.session.open(fromfilename, 'r')    # SSH treats all files as binary
                content = fromfile.read()
                filesize = len(content)
                tofile = botslib.opendata(tofilename, 'wb')
                tofile.write(content)
                tofile.close()
                fromfile.close()
            except:
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='sftp-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
            else:
                ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                ta_from.update(statust=DONE)
                if self.channeldict['remove']:
                    self.session.remove(fromfilename)
            finally:
                if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                    break

    @botslib.log_session
    def outcommunicate(self):
        ''' do ftp: send files. To be used via receive-dispatcher.
            each to be send file is transaction.
            each send file is transaction.
        '''
        #get right filename_mask & determine if fixed name (append) or files with unique names
        filename_mask = self.channeldict['filename'] if self.channeldict['filename'] else '*'
        if '{overwrite}' in filename_mask:
            filename_mask = filename_mask.replace('{overwrite}','')
            mode = 'w'
        else:
            mode = 'a'
        for row in botslib.query('''SELECT idta,filename,numberofresends
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                      AND status=%(status)s
                                      AND statust=%(statust)s
                                      AND tochannel=%(tochannel)s
                                        ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,
                                    'status':FILEOUT,'statust':OK}):
            try:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to = ta_from.copyta(status=EXTERNOUT)
                tofilename = self.filename_formatter(filename_mask,ta_from)
                fromfile = botslib.opendata(row['filename'], 'rb')
                tofile = self.session.open(tofilename, mode)    # SSH treats all files as binary
                tofile.write(fromfile.read())
                tofile.close()
                fromfile.close()
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,filename='sftp:/'+posixpath.join(self.dirpath,tofilename),numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename='sftp:/'+posixpath.join(self.dirpath,tofilename),numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)


class xmlrpc(_comsession):
    scheme = 'http'
    def connect(self):
        import xmlrpclib
        self.uri = botslib.Uri(scheme=self.scheme,username=self.channeldict['username'],password=self.channeldict['secret'],host=self.channeldict['host'],port=self.channeldict['port'],path=self.channeldict['path'])
        self.session = xmlrpclib.ServerProxy(self.uri.uri)


    @botslib.log_session
    def outcommunicate(self):
        ''' do xml-rpc: send files. To be used via receive-dispatcher.
            each to be send file is transaction.
            each send file is transaction.
        '''
        for row in botslib.query('''SELECT idta,filename,charset,numberofresends
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                    AND status=%(status)s
                                    AND statust=%(statust)s
                                    AND tochannel=%(tochannel)s ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,
                                    'status':FILEOUT,'statust':OK}):
            try:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to =   ta_from.copyta(status=EXTERNOUT)
                fromfile = botslib.opendata(row['fromfilename'], 'rb',row['charset'])
                content = fromfile.read()
                fromfile.close()
                tocall = getattr(self.session,self.channeldict['filename'])
                filename = tocall(content)
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename=self.uri.update(path=self.channeldict['path'],filename=str(filename)),numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)


    @botslib.log_session
    def incommunicate(self):
        startdatetime = datetime.datetime.now()
        while True:
            try:
                tocall = getattr(self.session,self.channeldict['path'])
                content = tocall()
                if content is None:
                    break   #nothing (more) to receive.
                ta_from = botslib.NewTransaction(filename=self.uri.update(path=self.channeldict['path'],filename=self.channeldict['filename']),
                                                    status=EXTERNIN,
                                                    fromchannel=self.channeldict['idchannel'],
                                                    idroute=self.idroute)
                ta_to =   ta_from.copyta(status=FILEIN)
                tofilename = str(ta_to.idta)
                tofile = botslib.opendata(tofilename, 'wb')
                simplejson.dump(content, tofile, skipkeys=False, ensure_ascii=False, check_circular=False)
                tofile.close()
                filesize = os.path.getsize(botslib.abspathdata(tofilename))
            except:
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='xmlprc-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
            else:
                ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                ta_from.update(statust=DONE)
            finally:
                if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                    break

class db(_comsession):
    ''' communicate with a database; directly read or write from a database.
        the user HAS to provide a userscript file in usersys/communicationscripts that does the actual import/export using **some** python database library.
        the userscript file should contain:
        - connect
        - (for incoming) incommunicate
        - (for outgoing) outcommunicate
        - disconnect
        Other parameters are passed, use them for your own convenience.
        Bots 'pickles' the results returned from the userscript (and unpickles for the translation).
    '''
    def connect(self):
        if self.userscript is None:
            raise ImportError(_(u'Channel "%(idchannel)s" is type "db", but no communicationscript exists.'),
                                {'idchannel':self.channeldict})
        #check functions bots assumes to be present in userscript:
        if not hasattr(self.userscript,'connect'):
            raise botslib.ScriptImportError(_(u'No function "connect" in imported communicationscript "%(communicationscript)s".'),
                                                {'communicationscript':self.scriptname})
        if self.channeldict['inorout'] == 'in' and not hasattr(self.userscript,'incommunicate'):
            raise botslib.ScriptImportError(_(u'No function "incommunicate" in imported communicationscript "%(communicationscript)s".'),
                                                {'communicationscript':self.scriptname})
        if self.channeldict['inorout'] == 'out' and not hasattr(self.userscript,'outcommunicate'):
            raise botslib.ScriptImportError(_(u'No function "outcommunicate" in imported communicationscript "%(communicationscript)s".'),
                                                {'communicationscript':self.scriptname})
        if not hasattr(self.userscript,'disconnect'):
            raise botslib.ScriptImportError(_(u'No function "disconnect" in imported communicationscript "%(communicationscript)s".'),
                                            {'communicationscript':self.scriptname})

        self.dbconnection = botslib.runscript(self.userscript,self.scriptname,'connect',channeldict=self.channeldict)

    @botslib.log_session
    def incommunicate(self):
        ''' read data from database.
            userscript should return a 'db_objects'.
            This can be one edi-message or several edi-messages.
            if a list or tuple is passed: each element of list/tuple is treated as seperate edi-message.
            if this is None, do nothing
            if this is a list/tuple, each member of the list is send as a separate 'message'
            if you want all information from userscript to be passed as one edi message: pass as dict, eg {'data': <list of queries>}
        '''
        db_objects = botslib.runscript(self.userscript,self.scriptname,'incommunicate',channeldict=self.channeldict,dbconnection=self.dbconnection)
        if not db_objects:      #there should be a useful db_objects; if not just return (do nothing)
            return              
        if not isinstance(db_objects,(list,tuple)):
            db_objects = [db_objects]   #a list or tuple is expected: pack received object in a list (list with one member). 

        for db_object in db_objects:
            try:
                ta_from = botslib.NewTransaction(filename=self.channeldict['path'],
                                                    status=EXTERNIN,
                                                    fromchannel=self.channeldict['idchannel'],
                                                    idroute=self.idroute)
                ta_to = ta_from.copyta(status=FILEIN)
                tofilename = str(ta_to.idta)
                tofile = botslib.opendata(tofilename,'wb')
                pickle.dump(db_object, tofile)
                tofile.close()
                filesize = os.path.getsize(botslib.abspathdata(tofilename))
            except:
                txt = botslib.txtexc()
                botslib.ErrorProcess(functionname='db-incommunicate',errortext=txt,channeldict=self.channeldict)
                ta_from.delete()
                ta_to.delete()
            else:
                ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                ta_from.update(statust=DONE)

    @botslib.log_session
    def outcommunicate(self):
        ''' write data to database.
        '''
        for row in botslib.query('''SELECT idta,filename,numberofresends
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                    AND status=%(status)s
                                    AND statust=%(statust)s
                                    AND tochannel=%(tochannel)s ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,'status':FILEOUT,'statust':OK}):
            try:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to = ta_from.copyta(status=EXTERNOUT)
                fromfile = botslib.opendata(row['filename'], 'rb')
                db_object = pickle.load(fromfile)
                fromfile.close()
                botslib.runscript(self.userscript,self.scriptname,'outcommunicate',channeldict=self.channeldict,dbconnection=self.dbconnection,db_object=db_object)
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,filename=self.channeldict['path'],numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename=self.channeldict['path'],numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)

    def disconnect(self):
        botslib.runscript(self.userscript,self.scriptname,'disconnect',channeldict=self.channeldict,dbconnection=self.dbconnection)



class communicationscript(_comsession):
    ''' 
    For running an userscript for communication.
    Examples of use:
    - call external communication program
    - call external program that extract messages from ERP-database
    - call external program that imports messages in ERP system
    - communication method not available in Bots ***or use sub-classing for this***
    - specialised I/O wishes; eg specific naming of output files. (eg including partner name) ***beter: use sub-classing or have more user exits***
    place of communicationscript: bots/usersys/communicationscripts
    name of communicationscript: same name as channel (the channelID)
    in this communicationscript some functions will be called:
    -   connect (required)
    -   main (optional, 'main' should handle files one by one)
    -   disconnect  (required)
    arguments: dict 'channel' which has all channel attributes
    more parameters/data for communicationscript:   hard code this in communicationscript; or use bots.ini
    Different ways of working:
    1. for incoming files (bots receives the files):
        1.1 connect puts all files in a directory, there is no 'main' function. bots can remove the files (if you use the 'remove' switch of the channel).
        1.2 connect only builds the connection, 'main' is a generator that passes the messages one by one (using 'yield'). bots can remove the files (if you use the 'remove' switch of the channel).
    2. for outgoing files (bots sends the files):
        2.1 if there is a 'main' function: the 'main' function is called by bots after writing each file. bots can remove the files (if you use the 'remove' switch of the channel).
        2.2 no 'main' function: the processing of all the files can be done in 'disconnect'. bots can remove the files (if you use the 'remove' switch of the channel).
    ''' 
    def connect(self):
        if self.userscript is None or not botslib.tryrunscript(self.userscript,self.scriptname,'connect',channeldict=self.channeldict):
            raise ImportError(_(u'Channel "%(idchannel)s" is type "communicationscript", but no communicationscript exists.'),
                                {'idchannel':self.channeldict})


    @botslib.log_session
    def incommunicate(self):
        startdatetime = datetime.datetime.now()
        if hasattr(self.userscript,'main'): #process files one by one; communicationscript has to be a generator
            for fromfilename in botslib.runscriptyield(self.userscript,self.scriptname,'main',channeldict=self.channeldict):
                try:
                    ta_from = botslib.NewTransaction(filename = fromfilename,
                                                    status = EXTERNIN,
                                                    fromchannel = self.channeldict['idchannel'],
                                                    idroute = self.idroute)
                    ta_to = ta_from.copyta(status = FILEIN)
                    #open fromfile
                    fromfile = open(fromfilename, 'rb')
                    filesize = os.fstat(fromfile.fileno()).st_size
                    #open tofile
                    tofilename = str(ta_to.idta)
                    tofile = botslib.opendata(tofilename, 'wb')
                    #copy
                    shutil.copyfileobj(fromfile,tofile,1048576)
                    fromfile.close()
                    tofile.close()
                except:
                    txt = botslib.txtexc()
                    botslib.ErrorProcess(functionname='communicationscript-incommunicate',errortext=txt,channeldict=self.channeldict)
                    ta_from.delete()
                    ta_to.delete()
                else:
                    ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                    ta_from.update(statust=DONE)
                    if self.channeldict['remove']:
                        os.remove(fromfilename)
                finally:
                    if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                        break
        else:   #all files have been set ready by external communicationscript using 'connect'.
            frompath = botslib.join(self.channeldict['path'], self.channeldict['filename'])
            filelist = [filename for filename in glob.iglob(frompath) if os.path.isfile(filename)]
            filelist.sort()
            for fromfilename in filelist:
                try:
                    ta_from = botslib.NewTransaction(filename = fromfilename,
                                                    status = EXTERNIN,
                                                    fromchannel = self.channeldict['idchannel'],
                                                    idroute = self.idroute)
                    ta_to = ta_from.copyta(status = FILEIN)
                    fromfile = open(fromfilename, 'rb')
                    tofilename = str(ta_to.idta)
                    tofile = botslib.opendata(tofilename, 'wb')
                    content = fromfile.read()
                    filesize = len(content)
                    tofile.write(content)
                    fromfile.close()
                    tofile.close()
                except:
                    txt = botslib.txtexc()
                    botslib.ErrorProcess(functionname='communicationscript-incommunicate',errortext=txt,channeldict=self.channeldict)
                    ta_from.delete()
                    ta_to.delete()
                else:
                    ta_to.update(filename=tofilename,statust=OK,filesize=filesize)
                    ta_from.update(statust=DONE)
                    if self.channeldict['remove']:
                        os.remove(fromfilename)
                finally:
                    if (datetime.datetime.now()-startdatetime).seconds >= self.maxsecondsperchannel:
                        break


    @botslib.log_session
    def outcommunicate(self):
        #check if output dir exists, else create it.
        outputdir = botslib.join(self.channeldict['path'])
        botslib.dirshouldbethere(outputdir)
        #get right filename_mask & determine if fixed name (append) or files with unique names
        filename_mask = self.channeldict['filename'] if self.channeldict['filename'] else '*'
        if '{overwrite}' in filename_mask:
            filename_mask = filename_mask.replace('{overwrite}','')
            mode = 'wb'
        else:
            mode = 'ab'
        #select the db-ta's for this channel
        for row in botslib.query(u'''SELECT idta,filename,numberofresends
                                    FROM ta
                                    WHERE idta>%(rootidta)s
                                    AND status=%(status)s
                                    AND statust=%(statust)s
                                    AND tochannel=%(tochannel)s ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,
                                    'status':FILEOUT,'statust':OK}):
            try:    #for each db-ta:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to =   ta_from.copyta(status=EXTERNOUT)
                tofilename = self.filename_formatter(filename_mask,ta_from)
                #open tofile
                tofilename = botslib.join(outputdir,tofilename)
                tofile = open(tofilename, mode)
                #open fromfile
                fromfile = botslib.opendata(row['filename'], 'rb')
                #copy
                shutil.copyfileobj(fromfile,tofile,1048576)
                fromfile.close()
                tofile.close()
                #one file is written; call external
                if botslib.tryrunscript(self.userscript,self.scriptname,'main',channeldict=self.channeldict,filename=tofilename,ta=ta_from):
                    if self.channeldict['remove']:
                        os.remove(tofilename)
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename=tofilename,numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)

    def disconnect(self):
        botslib.tryrunscript(self.userscript,self.scriptname,'disconnect',channeldict=self.channeldict)
        if self.channeldict['remove'] and not hasattr(self.userscript,'main'):  #if bots should remove the files, and all files are passed at once, delete these files.
            outputdir = botslib.join(self.channeldict['path'], self.channeldict['filename'])
            for filename in glob.iglob(outputdir):
                if os.path.isfile(filename):
                    try:
                        os.remove(filename)
                    except:
                        pass

class trash(_comsession):
    @botslib.log_session
    def outcommunicate(self):
        ''' does output of files to 'nothing' (trash it).
        '''
        #select the db-ta's for this channel
        for row in botslib.query(u'''SELECT idta,filename,numberofresends
                                       FROM ta
                                      WHERE idta>%(rootidta)s
                                        AND status=%(status)s
                                        AND statust=%(statust)s
                                        AND tochannel=%(tochannel)s
                                        ''',
                                    {'tochannel':self.channeldict['idchannel'],'rootidta':self.rootidta,
                                    'status':FILEOUT,'statust':OK}):
            try:    #for each db-ta:
                ta_from = botslib.OldTransaction(row['idta'])
                ta_to =   ta_from.copyta(status=EXTERNOUT)
            except:
                txt = botslib.txtexc()
                ta_to.update(statust=ERROR,errortext=txt,numberofresends=row['numberofresends']+1)
            else:
                ta_to.update(statust=DONE,filename='',numberofresends=row['numberofresends']+1)
            finally:
                ta_from.update(statust=DONE)

