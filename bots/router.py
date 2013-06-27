from django.utils.translation import ugettext as _
#bots-modules
import automaticmaintenance
import botslib
import botsglobal
import communication
import envelope
import preprocess
import transform
from botsconfig import *

@botslib.log_session
def rundispatcher(command,routestorun):
    ''' one run for each command
    '''
    classtocall = globals()[command]           #get the route class from this module
    botsglobal.currentrun = classtocall(command,routestorun)
    if botsglobal.currentrun.run():
        return botsglobal.currentrun.evaluate()      #return result of evaluation of run: nr of errors, 0 (no error)
    else:
        botsglobal.logger.info(_(u'Nothing to do in run.'))
        return 0      #return 0 (no error) 

class new(object):
    def __init__(self,command,routestorun):
        self.routestorun = routestorun
        self.command = command
        self.minta4query = botslib._Transaction.processlist[-1]     #the idta of rundispatcher is rootidat of run
        self.keep_track_if_outchannel_deferred = {}
        
    def run(self):
        for route in self.routestorun:
            botslib.setrouteid(route)
            self.router(route)
            botslib.setrouteid('')
        return True 
        
    @botslib.log_session
    def router(self,route):
        ''' for each route (as in self.routestorun).
            a route can have multiple parts (seq)
        '''
        self.minta4query_route = botslib._Transaction.processlist[-1]     #the idta of route
        foundroute = False
        for row in botslib.query('''SELECT idroute     ,
                                                 fromchannel_id as fromchannel,
                                                 tochannel_id as tochannel,
                                                 fromeditype,
                                                 frommessagetype,
                                                 alt,
                                                 frompartner_id as frompartner,
                                                 topartner_id as topartner,
                                                 toeditype,
                                                 tomessagetype,
                                                 seq,
                                                 frompartner_tochannel_id,
                                                 topartner_tochannel_id,
                                                 testindicator,
                                                 translateind,
                                                 defer,
                                                 zip_incoming,
                                                 zip_outgoing
                                        FROM routes
                                        WHERE idroute=%(idroute)s
                                        AND active=%(active)s
                                        ORDER BY seq''',
                                        {'idroute':route,'active':True}):
            routedict = dict(row)   #convert to real dictionary (as self.command is added to routedict)
            routedict['command'] = self.command     #this way command is passed to ohter functions.
            foundroute = True
            botsglobal.logger.info(_(u'Running route %(idroute)s %(seq)s'),routedict)
            self.routepart(routedict)
            #handle deferred-logic: mark if channel is deffered, umark if run
            self.keep_track_if_outchannel_deferred[routedict['tochannel']] = routedict['defer']
            botsglobal.logger.debug(u'Finished route %(idroute)s %(seq)s',routedict)
        if not foundroute:
            botsglobal.logger.warning(_(u'There is no (active) route "%(route)s".'),{'route':route})
        
    @botslib.log_session
    def routepart(self,routedict):
        ''' communication.run one route part. variants:
            -   a route can be a routescript
            -   a route can do only incoming
            -   a route can do only outgoing
            -   a route can do both incoming and outgoing
            -   at several points functions from a routescript are called - if function is in routescript
        '''
        self.minta4query_routepart = botslib._Transaction.processlist[-1]     #the idta of routepart
        #is there a user routescript?
        try:
            userscript,scriptname = botslib.botsimport('routescripts',routedict['idroute'])
        except ImportError:      #routescript is not there; other errors like syntax errors are not catched
            userscript = scriptname = None

        #if routescript has function 'main': communication.run 'main' (and do nothing else)
        if botslib.tryrunscript(userscript,scriptname,'main',routedict=routedict):
            return  #so: if function ' main' : communication.run only the routescript, nothing else.
        if not (userscript or routedict['fromchannel'] or routedict['tochannel'] or routedict['translateind']):
            raise botslib.ScriptError(_(u'Route "%(route)s" is empty: no routescript, not enough parameters.'),routedict)

        botslib.tryrunscript(userscript,scriptname,'start',routedict=routedict)

        #incoming part of route:
        #- incommunication
        #- assign attributes from route to incoming files
        #- preprocessing
        if routedict['fromchannel']:
            #only done for edi files from this route-part, this inchannel
            botslib.tryrunscript(userscript,scriptname,'preincommunication',routedict=routedict)
            communication.run(idchannel=routedict['fromchannel'],command=routedict['command'],idroute=routedict['idroute'],rootidta=self.get_minta4query_routepart())  #communication.run incommunication
            #add attributes from route to the received files;
            if routedict['command'] == 'rereceive':
                rootidta = self.get_minta4query()
            else:
                rootidta = self.get_minta4query_routepart()
            where = {'statust':OK,'status':FILEIN,'fromchannel':routedict['fromchannel'],'idroute':routedict['idroute'],'rootidta':rootidta}
            change = {'editype':routedict['fromeditype'],'messagetype':routedict['frommessagetype'],'frompartner':routedict['frompartner'],'topartner':routedict['topartner'],'alt':routedict['alt']}
            nr_of_incoming_files_for_channel = botslib.updateinfo(change=change,where=where)
            botslib.tryrunscript(userscript,scriptname,'postincommunication',routedict=routedict)
            if nr_of_incoming_files_for_channel:
                #unzip incoming files (if indicated)
                if routedict['zip_incoming'] == 1:               #unzip incoming (non-zipped gives error).
                    preprocess.preprocess(routedict=routedict,function=preprocess.botsunzip,rootidta=self.get_minta4query_routepart(),pass_non_zip=False)
                elif routedict['zip_incoming'] == 2:               #unzip incoming if zipped.
                    preprocess.preprocess(routedict=routedict,function=preprocess.botsunzip,rootidta=self.get_minta4query_routepart(),pass_non_zip=True)
                #run mailbag-module.
                if botsglobal.ini.getboolean('settings','compatibility_mailbag',False):
                    editypes_via_mailbag = ['mailbag']
                else:
                    editypes_via_mailbag = ['mailbag','edifact','x12','tradacoms']
                if routedict['fromeditype'] in editypes_via_mailbag:               #mailbag for the route.
                    preprocess.preprocess(routedict=routedict,function=preprocess.mailbag,rootidta=self.get_minta4query_routepart())

        #translate, merge, pass through: INFILE->MERGED
        if int(routedict['translateind']) == 1:
            #translate: for files in route
            botslib.tryrunscript(userscript,scriptname,'pretranslation',routedict=routedict)
            if routedict['command'] in ['rereceive',]:
                rootidta = self.get_minta4query()
            else:
                rootidta = self.get_minta4query_route()
            transform.translate(startstatus=FILEIN,endstatus=TRANSLATED,idroute=routedict['idroute'],rootidta=rootidta)
            botslib.tryrunscript(userscript,scriptname,'posttranslation',routedict=routedict)
            #**merge: for files in this route-part (the translated files)
            botslib.tryrunscript(userscript,scriptname,'premerge',routedict=routedict)
            envelope.mergemessages(startstatus=TRANSLATED,endstatus=MERGED,idroute=routedict['idroute'],rootidta=self.get_minta4query_routepart())
            botslib.tryrunscript(userscript,scriptname,'postmerge',routedict=routedict)
        elif routedict['translateind'] == 2:        #pass-through: pickup the incoming files and mark these as MERGED (==translation is finished)
            botslib.addinfo(change={'status':MERGED,'statust':OK},where={'status':FILEIN,'statust':OK,'idroute':routedict['idroute'],'rootidta':self.get_minta4query_route()})
        #NOTE: routedict['translateind'] == 0 than nothing will happen with the files in this route. 

        #ommunication outgoing channel: MERGED->RAWOUT
        if routedict['tochannel']:
            #**build query to add outchannel as attribute to outgoing files***
            #filter files in route for outchannel
            towhere = { 'status':MERGED,
                        'statust':OK,
                        'idroute':routedict['idroute'],
                        'editype':routedict['toeditype'],
                        'messagetype':routedict['tomessagetype'],
                        'testindicator':routedict['testindicator'],
                        }
            towhere = dict([(key, value) for key,value in towhere.iteritems() if value])   #remove nul-values from dict
            wherestring = ' AND '.join([key+'=%('+key+')s ' for key in towhere])
            if routedict['frompartner_tochannel_id']:   #use frompartner_tochannel in where-clause of query (partner/group dependent outchannel
                towhere['frompartner_tochannel_id'] = routedict['frompartner_tochannel_id']
                wherestring += ''' AND (frompartner=%(frompartner_tochannel_id)s
                                    OR frompartner in (SELECT from_partner_id
                                    FROM partnergroup
                                    WHERE to_partner_id=%(frompartner_tochannel_id)s )) '''
            if routedict['topartner_tochannel_id']:   #use topartner_tochannel in where-clause of query (partner/group dependent outchannel
                towhere['topartner_tochannel_id'] = routedict['topartner_tochannel_id']
                wherestring += ''' AND (topartner=%(topartner_tochannel_id)s
                                    OR topartner in (SELECT from_partner_id
                                    FROM partnergroup
                                    WHERE to_partner_id=%(topartner_tochannel_id)s )) '''
            toset = {'status':FILEOUT,'statust':OK,'tochannel':routedict['tochannel']}
            towhere['rootidta'] = self.get_minta4query_route()
            nr_of_outgoing_files_for_channel = botslib.addinfocore(change=toset,where=towhere,wherestring=wherestring)
            
            if nr_of_outgoing_files_for_channel:
                #**set asked confirmation/acknowledgements
                botslib.set_asked_confirmrules(routedict,rootidta=self.get_minta4query_routepart())
                #**zip outgoing
                #for files in this route-part for this out-channel
                if routedict['zip_outgoing'] == 1:               
                    preprocess.postprocess(routedict=routedict,function=preprocess.botszip,rootidta=self.get_minta4query_routepart())
                    
            #actual communication: run outgoing channel (if not deferred)
            #for all files in run that are for this channel (including the deferred ones from other routes)
            if not routedict['defer']:
                #determine range of idta to query: if channel was not deferred earlier in run: query only for route part else query for whole run
                if self.keep_track_if_outchannel_deferred.get(routedict['tochannel'],False) or routedict['command'] in ['resend','automaticretrycommunication']:
                    rootidta = self.get_minta4query()
                else:
                    rootidta = self.get_minta4query_routepart()
                if botslib.countoutfiles(idchannel=routedict['tochannel'],rootidta=rootidta):
                    botslib.tryrunscript(userscript,scriptname,'preoutcommunication',routedict=routedict)
                    communication.run(idchannel=routedict['tochannel'],command=routedict['command'],idroute=routedict['idroute'],rootidta=rootidta)
                    #in communication several things can go wrong.
                    #all outgoing files should have same status; that way all recomnnunication can be handled the same:
                    #- status EXTERNOUT statust DONE (if communication goes OK)
                    #- status EXTERNOUT status ERROR (if file is not communicatied)
                    #to have the same status for all outgoing files some manipulation is needed, eg in case no connection could be made.
                    botslib.addinfo(change={'status':EXTERNOUT,'statust':ERROR},where={'status':FILEOUT,'statust':OK,'tochannel':routedict['tochannel'],'rootidta':rootidta})
                    botslib.tryrunscript(userscript,scriptname,'postoutcommunication',routedict=routedict)
                
        botslib.tryrunscript(userscript,scriptname,'end',routedict=routedict)

        
    def evaluate(self):
        try:
            return automaticmaintenance.evaluate(self.command,self.get_minta4query())
        except:
            botsglobal.logger.exception(_(u'Error in automatic maintenance.'))
            return 1

    def get_minta4query(self):
        ''' get the first idta for queries etc in whole run.
        '''
        return self.minta4query

    def get_minta4query_route(self):
        ''' get the first idta for queries etc in route.
        '''
        return self.minta4query_route

    def get_minta4query_routepart(self):
        ''' get the first idta for queries etc in route-part.
        '''
        return self.minta4query_routepart
        

class crashrecovery(new):
    ''' a crashed run is rerun.
    '''
    def run(self):
        #get rootidta of crashed run
        for row in botslib.query('''SELECT MAX(idta) as crashed_idta
                                    FROM ta
                                    WHERE idta < %(rootidta_of_current_run)s
                                    AND script = 0 ''',
                                    {'rootidta_of_current_run':self.get_minta4query()}):
            self.minta4query_crash = row['crashed_idta']
        if not self.minta4query_crash:
            return False    #no run
        
        rootofcrashedrun = botslib.OldTransaction(self.minta4query_crash)
        rootofcrashedrun.update(statust=DONE)
        #clean up things from crash **********************************
        #delete run report
        botslib.changeq('''DELETE FROM report WHERE idta = %(rootofcrashedrun)s''',{'rootofcrashedrun':rootofcrashedrun.idta})
        #delete file reports
        botslib.changeq('''DELETE FROM filereport WHERE idta>%(rootofcrashedrun)s''',{'rootofcrashedrun':rootofcrashedrun.idta})
        #delete ta's after ERROR and OK for crashed merges
        mergedidtatodelete = set()
        for row in botslib.query('''SELECT child  FROM ta 
                                    WHERE idta > %(rootofcrashedrun)s
                                    AND statust = %(statust)s
                                    AND status != %(status)s
                                    AND child != 0 ''',
                                    {'rootofcrashedrun':rootofcrashedrun.idta,'status':PROCESS,'statust':OK}):
            mergedidtatodelete.add(row['child'])
        for idta in mergedidtatodelete:
            ta_object = botslib.OldTransaction(idta)
            ta_object.delete()
        #delete ta's after ERROR and OK for other
        for row in botslib.query('''SELECT idta  FROM ta 
                                    WHERE idta > %(rootofcrashedrun)s
                                    AND ( statust = %(statust1)s OR statust = %(statust2)s )
                                    AND status != %(status)s
                                    AND child = 0 ''',
                                    {'rootofcrashedrun':rootofcrashedrun.idta,'status':PROCESS,'statust1':OK,'statust2':ERROR}):
            ta_object = botslib.OldTransaction(row['idta'])
            ta_object.deletechildren()
        
        return super(crashrecovery, self).run()

    def get_minta4query(self):
        ''' get the first idta for queries etc in whole run.
        '''
        return self.minta4query_crash

    def get_minta4query_route(self):
        ''' get the first idta for queries etc in route.
        '''
        return self.minta4query_crash

    def get_minta4query_routepart(self):
        ''' get the first idta for queries etc in route-part.
        '''
        return self.minta4query_crash



class automaticretrycommunication(new):
    def run(self):
        ''' reinjects files with failed communication.
        '''
        #bots keeps track of last time automaticretrycommunication was done; reason: performance
        idta_lastretry = botslib.uniquecore('bots__automaticretrycommunication',updatewith=self.minta4query)
        for row in botslib.query('''SELECT MIN(idta) AS min_idta
                                    FROM filereport
                                    WHERE idta > %(idta_lastretry)s
                                    AND statust = %(statust)s ''',
                                    {'statust':ERROR,'idta_lastretry':idta_lastretry}):
            startidta = row['min_idta']
        if not startidta:
            return False    #no run
        do_retransmit = False
        for row in botslib.query('''SELECT idta,parent,numberofresends
                                    FROM ta
                                    WHERE idta > %(startidta)s
                                    AND status = %(status)s
                                    AND statust = %(statust)s ''',
                                    {'statust':ERROR,'status':EXTERNOUT,'startidta':startidta}):
            do_retransmit = True
            ta_outgoing = botslib.OldTransaction(row['idta'])
            ta_outgoing.update(retransmit=False,statust=RESEND)     #set retransmit back to False
            ta_resend = botslib.OldTransaction(row['parent'])  #parent ta with status RAWOUT; this is where the outgoing file is kept
            ta_externin = ta_resend.copyta(status=EXTERNIN,statust=DONE) #inject; status is DONE so this ta is not used further
            ta_externin.copyta(status=FILEOUT,statust=OK,numberofresends=row['numberofresends'])  #reinjected file is ready as new input

        if do_retransmit:
            return super(automaticretrycommunication, self).run()
        else:
            return False    #no run

        
class resend(new):
    def run(self):
        ''' prepare the files indicated by user to be resend. Return: indication if files should be resend.
            Resend does not have a good performance. The start query can take some time as whole ta tabel is scanned.
            AFAIK this can be improved by maintaining seperate list of files to resend.
        '''
        do_retransmit = False
        for row in botslib.query('''SELECT idta,parent,numberofresends
                                    FROM ta
                                    WHERE retransmit = %(retransmit)s
                                    AND status = %(status)s''',
                                    {'retransmit':True,'status':EXTERNOUT}):
            do_retransmit = True
            ta_outgoing = botslib.OldTransaction(row['idta'])
            ta_outgoing.update(retransmit=False,statust=RESEND)     #set retransmit back to False
            ta_resend = botslib.OldTransaction(row['parent'])  #parent ta with status RAWOUT; this is where the outgoing file is kept
            ta_externin = ta_resend.copyta(status=EXTERNIN,statust=DONE) #inject; status is DONE so this ta is not used further
            ta_externin.copyta(status=FILEOUT,statust=OK,numberofresends=row['numberofresends'])  #reinjected file is ready as new input
            
        if do_retransmit:
            return super(resend, self).run()
        else:
            return False    #no run


class rereceive(new):
    def run(self):
        ''' prepare the files indicated by user to be rereceived. Return: indication if files should be rereceived.
        '''
        do_retransmit = False
        for row in botslib.query('''SELECT idta
                                    FROM filereport
                                    WHERE retransmit = %(retransmit)s ''',
                                    {'retransmit':1}):
            do_retransmit = True
            botslib.changeq('''UPDATE filereport
                              SET retransmit = %(retransmit)s
                              WHERE idta = %(idta)s ''',
                              {'idta':row['idta'],'retransmit':0})
            for row2 in botslib.query('''SELECT idta
                                        FROM ta
                                        WHERE parent = %(parent)s ''',
                                        {'parent':row['idta']}):
                ta_rereceive = botslib.OldTransaction(row2['idta'])
                ta_externin = ta_rereceive.copyta(status=EXTERNIN,statust=DONE,parent=0) #inject; status is DONE so this ta is not used further
                ta_externin.copyta(status=FILEIN,statust=OK)  #reinjected file is ready as new input

        if do_retransmit:
            return super(rereceive, self).run()
        else:
            return False    #no run
