from django.utils.translation import ugettext as _
#bots-modules
import botslib
from botsconfig import *

def grammarread(editype,grammarname,typeofgrammarfile='grammars'):
    ''' dispatch function for class Grammar and subclasses.
        read whole grammar or only syntax (parameter 'typeofgrammarfile').
        directory from where grammar is via parameter 'typeofgrammarfile'.
    '''
    try:
        classtocall = globals()[editype]
    except KeyError:
        raise botslib.GrammarError(_(u'Read grammar for editype "%(editype)s" messagetype "%(messagetype)s", but editype is unknown.'),
                                        {'editype':editype,'messagetype':grammarname})
    if typeofgrammarfile == 'grammars':
        # read grammar for messagetype first, than syntax for envelope.
        messagegrammar = classtocall(typeofgrammarfile='grammars',editype=editype,grammarname=grammarname)
        #Get right syntax: start with classtocall.defaultsyntax, update with envelope.syntax, update again with messagetype.syntax
        syntax = classtocall.defaultsyntax.copy()
        envelope = messagegrammar.syntax.get('envelope') or classtocall.defaultsyntax['envelope']
        if envelope and envelope!=grammarname:
            try:
                envelopegrammar = classtocall(typeofgrammarfile='grammars',editype=editype,grammarname=envelope)
                syntax.update(envelopegrammar.syntax)
            except:
                pass
        syntax.update(messagegrammar.syntax)
        messagegrammar.syntax = syntax
        return messagegrammar
    elif typeofgrammarfile == 'envelope':
        #used when reading grammar for enveloping/outgoing. For 'noenvelope' this will not be done.
        # read syntax for messagetype first, than grammar for envelope.
        messagegrammar = classtocall(typeofgrammarfile='grammars',editype=editype,grammarname=grammarname)
        #Get right syntax: start with classtocall.defaultsyntax, update with envelope.syntax, update again with messagetype.syntax
        syntax = classtocall.defaultsyntax.copy()
        envelope = messagegrammar.syntax.get('envelope') or classtocall.defaultsyntax['envelope']
        if envelope != grammarname:
            try:
                envelopegrammar = classtocall(typeofgrammarfile='grammars',editype=editype,grammarname=envelope)
                syntax.update(envelopegrammar.syntax)
            except:
                envelopegrammar = messagegrammar
        syntax.update(messagegrammar.syntax)
        envelopegrammar.syntax = syntax
        return envelopegrammar
    else:   #typeofgrammarfile == 'partners':
        return classtocall(typeofgrammarfile='partners',editype=editype,grammarname=grammarname)


class Grammar(object):
    ''' Class for translation grammar. A grammar contains the description of an edi-file; this is used in reading or writing an edi file.
        Description of the grammar file: see user manual.
        The grammar is read from a grammar file.
        A grammar file has several grammar parts , eg 'structure' and 'recorddefs'.
        Grammar parts is either in the grammar part itself or a imported from another grammar-file (eg the edifact segments .

        in a grammar 'structure' is a list of dicts describing the sequence and relationships between the record(group)s:
            attributes of each record(group) in structure:
            -   ID       record id
            -   MIN      min #occurences record or group
            -   MAX      max #occurences record of group
            -   LEVEL    child-lex_records
            added after reading the grammar (so: not in grammar-file):
            -   MPATH    mpath of record
            -   FIELDS   (added from recordsdefs via lookup)
        in a grammar 'recorddefs' describes the (sub) fields for the lex_records:
        -   'recorddefs' is a dict where key is the recordID, value is list of (sub) fields
            each (sub)field is a tuple of (field or subfield)
            field is tuple of (ID, MANDATORY, LENGTH, FORMAT)
            subfield is tuple of (ID, MANDATORY, tuple of fields)

        every grammar-file is read once (default python import-machinery).
        The information in a grammar is checked and manipulated by bots.
        if a structure or recorddef has already been read, Bots skips most of the checks.
    '''
    _checkstructurerequired = True

    def __init__(self,typeofgrammarfile,editype,grammarname):
        self.module,self.grammarname = botslib.botsimport(typeofgrammarfile,editype + '.' + grammarname)
        #get syntax from grammar file
        syntaxfromgrammar = getattr(self.module, 'syntax',{})
        self.syntax = syntaxfromgrammar.copy()  #copy to get independent syntax-object
        if not isinstance(syntaxfromgrammar,dict):
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s": syntax is not a dict{}.'),
                                        {'grammar':self.grammarname})

        if typeofgrammarfile == 'partners':
            return
        #init rest of grammar
        self.nextmessage = getattr(self.module, 'nextmessage',None)
        self.nextmessage2 = getattr(self.module, 'nextmessage2',None)
        self.nextmessageblock = getattr(self.module, 'nextmessageblock',None)
        if self.nextmessage is None:
            if self.nextmessage2 is not None:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s": if nextmessage2: nextmessage has to be used.'),
                                            {'grammar':self.grammarname})
        else:
            if self.nextmessageblock is not None:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s": nextmessageblock and nextmessage not both allowed.'),
                                            {'grammar':self.grammarname})
        if self._checkstructurerequired:
            try:
                self._dorecorddefs()
            except:
                self.recorddefs['BOTS_1$@#%_error'] = True                  #mark structure has been read with errors
                raise
            else:
                self.recorddefs['BOTS_1$@#%_error'] = False                 #mark structure has been read and checked
            try:
                self._dostructure()
            except AttributeError:  #if grammarpart does not exist set to None; test required grammarpart elsewhere
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s": no structure, is required.'),
                                            {'grammar':self.grammarname})
            except:
                self.structure[0]['error'] = True                #mark the structure as having errors
                raise

    def _dorecorddefs(self):
        ''' 1. check the recorddefinitions for validity.
            2. adapt in field-lex_records: normalise length lists, set bool ISFIELD, etc
        '''
        try:
            self.recorddefs = getattr(self.module, 'recorddefs')
        except AttributeError:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s": no recorddefs.'),
                                        {'grammar':self.grammarname})
        if not isinstance(self.recorddefs,dict):
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s": recorddefs is not a dict{}.'),
                                            {'grammar':self.grammarname})
        #check if grammar is read & checked earlier in this run. If so, we can skip all checks.
        if 'BOTS_1$@#%_error' in self.recorddefs:   #if checked before
            if self.recorddefs['BOTS_1$@#%_error']:     #if grammar had errors
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s" has error that is already reported in this run.'),
                                            {'grammar':self.grammarname})
            return      #no error, skip checks
        for recordid ,fields in self.recorddefs.iteritems():
            if not isinstance(recordid,basestring):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": is not a string.'),
                                                {'grammar':self.grammarname,'record':recordid})
            if not recordid:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": recordid with empty string.'),
                                            {'grammar':self.grammarname,'record':recordid})
            if not isinstance(fields,list):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": no correct fields found.'),
                                            {'grammar':self.grammarname,'record':recordid})
            if isinstance(self,(xml,json)):
                if len (fields) < 1:
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": too few fields.'),
                                                {'grammar':self.grammarname,'record':recordid})
            else:
                if len (fields) < 2:
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": too few fields.'),
                                                {'grammar':self.grammarname,'record':recordid})

            has_botsid = False   #to check if BOTSID is present
            fieldnamelist = []  #to check for double fieldnames
            for field in fields:
                self._checkfield(field,recordid)
                if not field[ISFIELD]:  # if composite
                    for sfield in field[SUBFIELDS]:
                        self._checkfield(sfield,recordid)
                        if sfield[ID] in fieldnamelist:
                            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": field "%(field)s" appears twice. Field names should be unique within a record.'),
                                                        {'grammar':self.grammarname,'record':recordid,'field':sfield[ID]})
                        fieldnamelist.append(sfield[ID])
                else:
                    if field[ID] == 'BOTSID':
                        has_botsid = True
                    if field[ID] in fieldnamelist:
                        raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": field "%(field)s" appears twice. Field names should be unique within a record.'),
                                                        {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
                    fieldnamelist.append(field[ID])

            if not has_botsid:   #there is no field 'BOTSID' in record
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s": no field BOTSID.'),
                                                {'grammar':self.grammarname,'record':recordid})
        if (self.syntax.get('noBOTSID') or self.__class__.defaultsyntax.get('noBOTSID')) and len(self.recorddefs) != 1:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s": if syntax["noBOTSID"]: there can be only one record in recorddefs.'),
                                            {'grammar':self.grammarname})
        if self.nextmessageblock is not None and len(self.recorddefs) != 1:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s": if nextmessageblock: there can be only one record in recorddefs.'),
                                            {'grammar':self.grammarname})

    def _checkfield(self,field,recordid):
        #'normalise' field: make list equal length
        len_field = len(field)
        if len_field == 3:  # that is: composite
            field +=[None,False,None,None,'A',1]
        elif len_field == 4:               # that is: field (not a composite)
            field +=[True,0,0,'A',1]
        #each field is now equal length list
        elif len_field == 9:               # this happens when there are errors in a table and table is read again
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s": error in grammar; error is already reported in this run.'),
                                            {'grammar':self.grammarname})
        else:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": list has invalid number of arguments.'),
                                            {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
        if not isinstance(field[ID],basestring) or not field[ID]:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": fieldID has to be a string.'),
                                            {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
        if isinstance(field[MANDATORY],basestring):
            if field[MANDATORY] not in 'MC':
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": mandatory/conditional must be "M" or "C".'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
            field[MANDATORY] = 0 if field[MANDATORY]=='C' else 1
        elif isinstance(field[MANDATORY],tuple):
            if not isinstance(field[MANDATORY][0],basestring):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": mandatory/conditional must be "M" or "C".'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
            if field[MANDATORY][0] not in 'MC':
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": mandatory/conditional must be "M" or "C".'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
            if not isinstance(field[MANDATORY][1],int):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": number of repeats must be integer.'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
            field[MAXREPEAT] = field[MANDATORY][1]
            field[MANDATORY] = 0 if field[MANDATORY][0]=='C' else 1
        else:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": mandatory/conditional has to be a string (or tuple in case of repeating field).'),
                                            {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
        if field[ISFIELD]:  # that is: field, and not a composite
            #get MINLENGTH (from tuple or if fixed
            if isinstance(field[LENGTH],(int,float)):
                if isinstance(self,fixed):
                    field[MINLENGTH] = field[LENGTH]
            elif isinstance(field[LENGTH],tuple):
                if not isinstance(field[LENGTH][0],(int,float)):
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": min length "%(min)s" has to be a number.'),
                                                    {'grammar':self.grammarname,'record':recordid,'field':field[ID],'min':field[LENGTH][0]})
                if not isinstance(field[LENGTH][1],(int,float)):
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": max length "%(max)s" has to be a number.'),
                                                    {'grammar':self.grammarname,'record':recordid,'field':field[ID],'max':field[LENGTH][1]})
                if field[LENGTH][0] > field[LENGTH][1]:
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": min length "%(min)s" must be > max length "%(max)s".'),
                                                    {'grammar':self.grammarname,'record':recordid,'field':field[ID],'min':field[LENGTH][0],'max':field[LENGTH][1]})
                field[MINLENGTH] = field[LENGTH][0]
                field[LENGTH] = field[LENGTH][1]
            else:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": length "%(len)s" has to be number or (min,max).'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID],'len':field[LENGTH]})
            if field[LENGTH] < 1:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": length "%(len)s" has to be at least 1.'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID],'len':field[LENGTH]})
            if field[MINLENGTH] < 0:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": minlength "%(len)s" has to be at least 0.'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID],'len':field[LENGTH]})
            #format
            if not isinstance(field[FORMAT],basestring):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": format "%(format)s" has to be a string.'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID],'format':field[FORMAT]})
            self._manipulatefieldformat(field,recordid)
            if field[BFORMAT] in 'NIR':
                if isinstance(field[LENGTH],float):
                    length,nrdecimals = divmod(field[LENGTH],1)     #divide by 1 to get whole number and leftover
                    field[DECIMALS] = int( round(nrdecimals*10) )   #fill DECIMALS with leftover*10. Does not work for more than 9 decimal places...
                    field[LENGTH] = int(length)
                    if field[DECIMALS] >= field[LENGTH]:
                        raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": field length "%(len)s" has to be greater that nr of decimals "%(decimals)s".'),
                                                        {'grammar':self.grammarname,'record':recordid,'field':field[ID],'decimals':field[DECIMALS]})
                if isinstance(field[MINLENGTH],float):
                    field[MINLENGTH] = int(field[MINLENGTH]//1)
            else:   #if format 'R', A, D, T
                if isinstance(field[LENGTH],float):
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": if format "%(format)s", no length "%(len)s".'),
                                                    {'grammar':self.grammarname,'record':recordid,'field':field[ID],'format':field[FORMAT],'len':field[LENGTH]})
                if isinstance(field[MINLENGTH],float):
                    raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": if format "%(format)s", no minlength "%(len)s".'),
                                                    {'grammar':self.grammarname,'record':recordid,'field':field[ID],'format':field[FORMAT],'len':field[MINLENGTH]})
        else:       #check composite
            if not isinstance(field[SUBFIELDS],list):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": is a composite field, has to have subfields.'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID]})
            if len(field[SUBFIELDS]) < 2:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s" has < 2 sfields.'),
                                                {'grammar':self.grammarname,'record':recordid,'field':field[ID]})

    def _linkrecorddefs2structure(self,structure):
        ''' recursive
            for each record in structure: add the pointer to the right recorddefinition.
        '''
        for i in structure:
            try:
                i[FIELDS] = self.recorddefs[i[ID]]
            except KeyError:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s": in recorddef no record "%(record)s".'),
                                            {'grammar':self.grammarname,'record':i[ID]})
            if LEVEL in i:
                self._linkrecorddefs2structure(i[LEVEL])

    def _dostructure(self):
        ''' 1. check the structure for validity.
            2. adapt in structure: Add keys: mpath, count
            3. remember that structure is checked and adapted (so when grammar is read again, no checking/adapt needed)
        '''
        self.structure = getattr(self.module, 'structure')
        if len(self.structure) != 1:                        #every structure has only 1 root!!
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure: only one root record allowed.'),{'grammar':self.grammarname})
        #check if structure is read & checked earlier in this run. If so, we can skip all checks.
        if 'error' in self.structure[0]:
            pass        # grammar has been read before, but there are errors. Do nothing here, same errors will be raised again.
        elif MPATH in self.structure[0]:
            return      # grammar has been read before, with no errors. Do no checks.
        self._checkstructure(self.structure,[])
        if self.syntax.get('checkcollision') or self.__class__.defaultsyntax.get('checkcollision'):
            self._checkbackcollision(self.structure)
            self._checknestedcollision(self.structure)
        self._checkbotscollision(self.structure)
        self._linkrecorddefs2structure(self.structure)

    def _checkstructure(self,structure,mpath):
        ''' Recursive
            1.   Check structure.
            2.   Add keys: mpath, count
        '''
        if not isinstance(structure,list):
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": not a list.'),
                                        {'grammar':self.grammarname,'mpath':mpath})
        for i in structure:
            if not isinstance(i,dict):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record should be a dict: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if ID not in i:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record without ID: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if not isinstance(i[ID],basestring):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": recordid of record is not a string: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if not i[ID]:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": recordid of record is empty: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if MIN not in i:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record without MIN: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if MAX not in i:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record without MAX: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if not isinstance(i[MIN],int):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record where MIN is not whole number: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if not isinstance(i[MAX],int):
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record where MAX is not whole number: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if not i[MAX]:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": MAX is zero: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':i})
            if i[MIN] > i[MAX]:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure, at "%(mpath)s": record where MIN > MAX: "%(record)s".'),
                                            {'grammar':self.grammarname,'mpath':mpath,'record':str(i)[:100]})
            i[MPATH] = mpath + [[i[ID]]]
            if LEVEL in i:
                self._checkstructure(i[LEVEL],i[MPATH])

    def _checkbackcollision(self,structure,collision=None):
        ''' Recursive.
            Check if grammar has back-collision problem. A message with collision problems is ambiguous.
            Case 1:  AAA BBB AAA
            Case 2:  AAA     BBB
                     BBB CCC
        '''
        if not collision:
            collision = []
        headerissave = False
        for i in structure:
            #~ print 'check back segment:',i[MPATH], 'with list:',collision
            if i[ID] in collision:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure: back-collision detected at record "%(mpath)s".'),
                                            {'grammar':self.grammarname,'mpath':i[MPATH]})
            if i[MIN]:
                headerissave = True
                if i[MIN] == i[MAX]:    #so: fixed number of occurences; can not lead to collision as  is always clear where in structure record is
                    collision = []      #NOTE: this is mainly used for MIN=1, MAX=1
                else:
                    collision = [i[ID]] #previous lex_records do not cause collision.
            else:
                collision.append(i[ID])
            if LEVEL in i:
                if i[MIN] == i[MAX] == 1:
                    returncollision,returnheaderissave = self._checkbackcollision(i[LEVEL])
                else:
                    returncollision,returnheaderissave = self._checkbackcollision(i[LEVEL],[i[ID]])
                collision += returncollision
                if returnheaderissave and i[ID] in collision:  #if one of segment(groups) is required, there is always a segment after the header segment; so remove header from nowcollision:
                    collision.remove(i[ID])
        return collision,headerissave    #collision is used to update on higher level; cleared indicates the header segment can not collide anymore

    def _checkbotscollision(self,structure):
        ''' Recursive.
            Within one level: if twice the same tag: use BOTSIDNR.
        '''
        collision = {}
        for i in structure:
            if i[ID] in collision:
                #~ raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure: bots-collision detected at record "%(mpath)s".'),{'grammar':self.grammarname,'mpath':i[MPATH]})
                i[BOTSIDNR] = str(collision[i[ID]] + 1)
                collision[i[ID]] = collision[i[ID]] + 1
            else:
                i[BOTSIDNR] = u'1'
                collision[i[ID]] = 1
            if LEVEL in i:
                self._checkbotscollision(i[LEVEL])
        return

    def _checknestedcollision(self,structure,collision=None):
        ''' Recursive.
            Check if grammar has nested-collision problem. A message with collision problems is ambiguous.
            Case 1: AAA
                    BBB CCC
                        AAA
        '''
        if not collision:
            levelcollision = []
        else:
            levelcollision = collision[:]
        for i in reversed(structure):
            if LEVEL in i:
                if i[MIN] == i[MAX] == 1:
                    isa_safeheadersegment = self._checknestedcollision(i[LEVEL],levelcollision)
                else:
                    isa_safeheadersegment = self._checknestedcollision(i[LEVEL],levelcollision + [i[ID]])
            else:
                isa_safeheadersegment = False
            #~ print 'check nested segment',checkthissegment, i[MPATH], 'with list',levelcollision
            if isa_safeheadersegment or i[MIN] == i[MAX]:    #fixed number of occurences. this can be handled umambigiously: no check needed
                pass   #no check needed
            elif i[ID] in levelcollision:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s", in structure: nesting collision detected at record "%(mpath)s".'),
                                            {'grammar':self.grammarname,'mpath':i[MPATH]})
            if i[MIN]:
                levelcollision = []   #empty uppercollision
        return not bool(levelcollision)



    def display(self,structure,level=0):
        ''' Draw grammar, with indentation for levels.
            For debugging.
        '''
        for i in structure:
            print 'Record: ',i[MPATH],i
            for field in i[FIELDS]:
                print '    Field: ',field
            if LEVEL in i:
                self.display(i[LEVEL],level+1)

    #bots interpretats the format from the grammer; left side are the allowed values; right side are the internal forams bots uses.
    #the list directly below are the default values for the formats, subclasses can have their own list.
    #this makes it possible to use x12-formats for x12, edifact-formats for edifact etc
    formatconvert = {
        'A':'A',        #alfanumerical
        'AN':'A',       #alfanumerical
        #~ 'AR':'A',       #right aligned alfanumerical field, used in fixed lex_records.
        'D':'D',        #date
        'DT':'D',       #date-time
        'T':'T',        #time
        'TM':'T',       #time
        'N':'N',        #numerical, fixed decimal. Fixed nr of decimals; if no decimal used: whole number, integer
        #~ 'NL':'N',       #numerical, fixed decimal. In fixed format: no preceding zeros, left aligned,
        #~ 'NR':'N',       #numerical, fixed decimal. In fixed format: preceding blancs, right aligned,
        'R':'R',        #numerical, any number of decimals; the decimal point is 'floating'
        #~ 'RL':'R',       #numerical, any number of decimals. fixed: no preceding zeros, left aligned
        #~ 'RR':'R',       #numerical, any number of decimals. fixed: preceding blancs, right aligned
        'I':'I',        #numercial, implicit decimal
        }
    def _manipulatefieldformat(self,field,recordid):
        try:
            field[BFORMAT] = self.formatconvert[field[FORMAT]]
        except KeyError:
            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record "%(record)s", field "%(field)s": format "%(format)s" has to be one of "%(keys)s".'),
                                        {'grammar':self.grammarname,'record':recordid,'field':field[ID],'format':field[FORMAT],'keys':self.formatconvert.keys()})

#grammar subclasses. contain the defaultsyntax
class test(Grammar):
    defaultsyntax = {
        'checkcollision':True,
        'noBOTSID':False,
        }
class csv(Grammar):
    defaultsyntax = {
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'add_crlfafterrecord_sep':'',
        'allow_lastrecordnotclosedproperly':False,  #in csv sometimes the last record is no closed correctly. This is related to communciation over email. Beware: when using this, other checks will not be enforced!
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':True,
        'checkunknownentities': True,
        'contenttype':'text/csv',
        'decimaal':'.',
        'envelope':'',
        'escape':"",
        'field_sep':':',
        'forcequote': 1,            #(if quote_char is set) 0:no force: only quote if necessary:1:always force: 2:quote if alfanumeric
        'lengthnumericbare':False,
        'merge':True,
        'noBOTSID':False,
        'pass_all':True,
        'quote_char':"'",
        'record_sep':"\r\n",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'skip_firstline':False,
        'stripfield_sep':False, #safe choice, as csv is no real standard
        'triad':'',
        'wrap_length':0,     #for producing wrapped format, where a file consists of fixed length lex_records ending with crr/lf. Often seen in mainframe, as400
        }
class excel(csv):
    pass
class fixed(Grammar):
    formatconvert = {
        'A':'A',        #alfanumerical
        'AN':'A',       #alfanumerical
        'AR':'A',       #right aligned alfanumerical field, used in fixed lex_records.
        'D':'D',        #date
        'DT':'D',       #date-time
        'T':'T',        #time
        'TM':'T',       #time
        'N':'N',        #numerical, fixed decimal. Fixed nr of decimals; if no decimal used: whole number, integer
        'NL':'N',       #numerical, fixed decimal. In fixed format: no preceding zeros, left aligned,
        'NR':'N',       #numerical, fixed decimal. In fixed format: preceding blancs, right aligned,
        'R':'R',        #numerical, any number of decimals; the decimal point is 'floating'
        'RL':'R',       #numerical, any number of decimals. fixed: no preceding zeros, left aligned
        'RR':'R',       #numerical, any number of decimals. fixed: preceding blancs, right aligned
        'I':'I',        #numercial, implicit decimal
        }
    defaultsyntax = {
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'add_crlfafterrecord_sep':'',
        'charset':'us-ascii',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':True,
        'checkfixedrecordtoolong':True,
        'checkfixedrecordtooshort':False,
        'checkunknownentities': True,
        'contenttype':'text/plain',
        'decimaal':'.',
        #~ 'endrecordID':3,
        'envelope':'',
        'escape':'',
        'field_sep':'',
        'forcequote':0,         #csv only
        'lengthnumericbare':False,
        'merge':True,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':"",
        'record_sep':"\r\n",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        #~ 'startrecordID':0,
        'stripfield_sep':False,
        'triad':'',
        'wrap_length':0,     #for producing wrapped format, where a file consists of fixed length lex_records ending with crr/lf. Often seen in mainframe, as400
        }
    is_first_record = True
    def _linkrecorddefs2structure(self,structure):
        ''' for class fixed: more checking etc is done.
            recursive
            for each record in structure: add the pointer to the right recorddefinition.
        '''
        for i in structure:
            try:
                i[FIELDS] = self.recorddefs[i[ID]]
            except KeyError:
                raise botslib.GrammarError(_(u'Grammar "%(grammar)s": in recorddef no record "%(record)s".'),
                                            {'grammar':self.grammarname,'record':i[ID]})
            #determine start/end of BOTSID; check if pos and length BOTSID is the same for all records.
            position_in_record = 0
            for field in i[FIELDS]:
                if field[ID] == 'BOTSID':
                    if self.is_first_record:     #set startrecordID, endrecordID
                        self.is_first_record = False
                        startrecordID = position_in_record
                        endrecordID = position_in_record + field[LENGTH]
                        syntaxfromgrammar = getattr(self.module, 'syntax',{})      #original grammar systax has to be changed, so get it.
                        self.syntax['startrecordID'] = startrecordID            #also change in the copy made.
                        self.syntax['endrecordID'] = endrecordID
                        syntaxfromgrammar['startrecordID'] = startrecordID
                        syntaxfromgrammar['endrecordID'] = endrecordID
                    else:        #check startrecordID, endrecordID
                        if self.syntax['startrecordID'] != position_in_record or self.syntax['endrecordID'] != position_in_record + field[LENGTH]:
                            raise botslib.GrammarError(_(u'Grammar "%(grammar)s", record %(key)s: position and length of BOTSID should be equal in all records.'),
                                                    {'grammar':self.grammarname,'key':i[ID]})
                    break
                position_in_record += field[LENGTH]
            #calculate recordlength
            i[F_LENGTH] = sum([field[LENGTH] for field in i[FIELDS]])
            #go recursive
            if LEVEL in i:
                self._linkrecorddefs2structure(i[LEVEL])


class idoc(fixed):
    defaultsyntax = {
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'add_crlfafterrecord_sep':'',
        'automaticcount':True,
        'charset':'us-ascii',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':True,
        'checkfixedrecordtoolong':False,
        'checkfixedrecordtooshort':False,
        'checkunknownentities': True,
        'contenttype':'text/plain',
        'decimaal':'.',
        #~ 'endrecordID':10,
        'envelope':'',
        'escape':'',
        'field_sep':'',
        'forcequote':0,         #csv only
        'lengthnumericbare':False,
        'merge':False,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':"",
        'record_sep':"\r\n",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        #~ 'startrecordID':0,
        'stripfield_sep':False,
        'triad':'',
        'wrap_length':0,     #for producing wrapped format, where a file consists of fixed length lex_records ending with crr/lf. Often seen in mainframe, as400
        'MANDT':'0',
        'DOCNUM':'0',
        }
class xml(Grammar):
    defaultsyntax = {
        'add_crlfafterrecord_sep':'',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'attributemarker':'__',
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':False,
        'checkunknownentities': True,
        'contenttype':'text/xml ',
        'decimaal':'.',
        'DOCTYPE':'',                   #doctype declaration to use in xml header. 'DOCTYPE': 'mydoctype SYSTEM "mydoctype.dtd"'  will lead to: <!DOCTYPE mydoctype SYSTEM "mydoctype.dtd">
        'envelope':'',
        'extra_character_entity':{},    #additional character entities to resolve when parsing XML; mostly html character entities. Not in python 2.4. Example: {'euro':u'','nbsp':unichr(160),'apos':u'\u0027'}
        'escape':'',
        'field_sep':'',
        'forcequote':0,                 #csv only
        'indented':False,               #False: xml output is one string (no cr/lf); True: xml output is indented/human readable
        'lengthnumericbare':False,
        'merge':False,
        'namespace_prefixes':None,  #to over-ride default namespace prefixes (ns0, ns1 etc) for outgoing xml. is a list, consisting of tuples, each tuple consists of prefix and uri.
                                    #Example: 'namespace_prefixes':[('orders','http://www.company.com/EDIOrders'),]
        'noBOTSID':False,
        'pass_all':False,
        'processing_instructions': None,    #to generate processing instruction in xml prolog. is a list, consisting of tuples, each tuple consists of type of instruction and text for instruction.
                                            #Example: 'processing_instructions': [('xml-stylesheet' ,'href="mystylesheet.xsl" type="text/xml"'),('type-of-ppi' ,'attr1="value1" attr2="value2"')]
                                            #leads to this output in xml-file:  <?xml-stylesheet href="mystylesheet.xsl" type="text/xml"?><?type-of-ppi attr1="value1" attr2="value2"?>
        'quote_char':"",
        'record_sep':"",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'standalone':None,      #as used in xml prolog; values: 'yes' , 'no' or None (not used)
        'stripfield_sep':False,
        'triad':'',
        'version':'1.0',        #as used in xml prolog
        }
class xmlnocheck(xml):
    _checkstructurerequired = False
    defaultsyntax = {
        'add_crlfafterrecord_sep':'',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'attributemarker':'__',
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':False,
        'checkunknownentities': False,
        'contenttype':'text/xml ',
        'decimaal':'.',
        'DOCTYPE':'',                   #doctype declaration to use in xml header. DOCTYPE = 'mydoctype SYSTEM "mydoctype.dtd"'  will lead to: <!DOCTYPE mydoctype SYSTEM "mydoctype.dtd">
        'envelope':'',
        'escape':'',
        'extra_character_entity':{},    #additional character entities to resolve when parsing XML; mostly html character entities. Not in python 2.4. Example: {'euro':u'','nbsp':unichr(160),'apos':u'\u0027'}
        'field_sep':'',
        'forcequote':0,                 #csv only
        'indented':False,               #False: xml output is one string (no cr/lf); True: xml output is indented/human readable
        'lengthnumericbare':False,
        'merge':False,
        'namespace_prefixes':None,  #to over-ride default namespace prefixes (ns0, ns1 etc) for outgoing xml. is a list, consisting of tuples, each tuple consists of prefix and uri.
                                    #Example: 'namespace_prefixes':[('orders','http://www.company.com/EDIOrders'),]
        'noBOTSID':False,
        'pass_all':False,
        'processing_instructions': None,    #to generate processing instruction in xml prolog. is a list, consisting of tuples, each tuple consists of type of instruction and text for instruction.
                                            #Example: processing_instructions': [('xml-stylesheet' ,'href="mystylesheet.xsl" type="text/xml"'),('type-of-ppi' ,'attr1="value1" attr2="value2"')]
                                            #leads to this output in xml-file:  <?xml-stylesheet href="mystylesheet.xsl" type="text/xml"?><?type-of-ppi attr1="value1" attr2="value2"?>
        'quote_char':"",
        'record_sep':"",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'standalone':None,      #as used in xml prolog; values: 'yes' , 'no' or None (not used)
        'stripfield_sep':False,
        'triad':'',
        'version':'1.0',        #as used in xml prolog
        }
class template(Grammar):
    #20120101 depreciated. use class templatehtml
    _checkstructurerequired = False
    defaultsyntax = { \
        'add_crlfafterrecord_sep':'',
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':False,
        'contenttype':'text/xml',
        'checkunknownentities': True,
        'decimaal':'.',
        'envelope':'template',
        'envelope-template':'',
        'escape':'',
        'field_sep':'',
        'forcequote':0, #csv only
        'lengthnumericbare':False,
        'merge':True,
        'noBOTSID':False,
        'output':'xhtml-strict',
        'quote_char':"",
        'pass_all':False,
        'record_sep':"",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'stripfield_sep':False,
        'triad':'',
        }
class templatehtml(Grammar):
    _checkstructurerequired = False
    defaultsyntax = { \
        'add_crlfafterrecord_sep':'',
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':False,
        'contenttype':'text/xml',
        'checkunknownentities': True,
        'decimaal':'.',
        'envelope':'templatehtml',
        'envelope-template':'',
        'escape':'',
        'field_sep':'',
        'forcequote':0, #csv only
        'lengthnumericbare':False,
        'merge':True,
        'noBOTSID':False,
        'output':'xhtml-strict',
        'quote_char':"",
        'pass_all':False,
        'record_sep':"",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'stripfield_sep':False,
        'triad':'',
        }
class edifact(Grammar):
    defaultsyntax = {
        'add_crlfafterrecord_sep':'\r\n',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'charset':'UNOA',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':True,
        'checkunknownentities': True,
        'contenttype':'application/EDIFACT',
        'decimaal':'.',
        'envelope':'edifact',
        'escape':'?',
        'field_sep':'+',
        'forcequote':0, #csv only
        'forceUNA' : False,
        'lengthnumericbare':True,
        'merge':True,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':'',
        'record_sep':"'",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'*',
        'sfield_sep':':',
        'skip_char':'\r\n',
        'stripfield_sep':True,
        'triad':'',
        'version':'3',
        'wrap_length':0,     #for producing wrapped format, where a file consists of fixed length lex_records ending with crr/lf. Often seen in mainframe, as400
        'UNB.S001.0080':'',
        'UNB.S001.0133':'',
        'UNB.S002.0007':'14',
        'UNB.S002.0008':'',
        'UNB.S002.0042':'',
        'UNB.S003.0007':'14',
        'UNB.S003.0014':'',
        'UNB.S003.0046':'',
        'UNB.S005.0022':'',
        'UNB.S005.0025':'',
        'UNB.0026':'',
        'UNB.0029':'',
        'UNB.0031':'',
        'UNB.0032':'',
        'UNB.0035':'0',
        }
    formatconvert = {
        'A':'A',
        'AN':'A',
        'N':'R',
        }
class x12(Grammar):
    defaultsyntax = {
        'add_crlfafterrecord_sep':'\r\n',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'charset':'us-ascii',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':True,
        'checkunknownentities': True,
        'contenttype':'application/X12',
        'decimaal':'.',
        'envelope':'x12',
        'escape':'',
        'field_sep':'*',
        'forcequote':0, #csv only
        'functionalgroup'    :  'XX',
        'lengthnumericbare':True,
        'merge':True,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':'',
        'record_sep':"~",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'replacechar':'',       #if separator found, replace by this character; if replacechar is an empty string: raise error
        'reserve':'^',
        'sfield_sep':'>',    #advised '\'?
        'skip_char':'\r\n',
        'stripfield_sep':True,
        'triad':'',
        'version':'00403',
        'wrap_length':0,     #for producing wrapped format, where a file consists of fixed length lex_records ending with cr/lf.
        'ISA01':'00',
        'ISA02':'          ',
        'ISA03':'00',
        'ISA04':'          ',
        'ISA05':'01',
        'ISA07':'01',
        'ISA11':'U',        #since ISA version 00403 this is the reserve/repetition separator. Bots does not use this anymore for ISA version >00403
        'ISA14':'1',
        'ISA15':'P',
        'GS07':'X',
        }
    formatconvert = {
        'AN':'A',
        'DT':'D',
        'TM':'T',
        'N':'I',
        'N0':'I',
        'N1':'I',
        'N2':'I',
        'N3':'I',
        'N4':'I',
        'N5':'I',
        'N6':'I',
        'N7':'I',
        'N8':'I',
        'N9':'I',
        'R':'R',
        'B':'A',
        'ID':'A',
        }
    def _manipulatefieldformat(self,field,recordid):
        super(x12,self)._manipulatefieldformat(field,recordid)
        if field[BFORMAT] == 'I':
            if field[FORMAT][1:]:
                field[DECIMALS] = int(field[FORMAT][1])
            else:
                field[DECIMALS] = 0
class json(Grammar):
    defaultsyntax = {
        'add_crlfafterrecord_sep':'',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':False,
        'checkunknownentities': True,
        'contenttype':'text/xml ',
        'decimaal':'.',
        'defaultBOTSIDroot':'ROOT',
        'envelope':'',
        'escape':'',
        'field_sep':'',
        'forcequote':0, #csv only
        'indented':False,               #False:  output is one string (no cr/lf); True:  output is indented/human readable
        'lengthnumericbare':False,
        'merge':False,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':"",
        'record_sep':"",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'stripfield_sep':False,
        'triad':'',
        }
class jsonnocheck(json):
    _checkstructurerequired = False
    defaultsyntax = {
        'add_crlfafterrecord_sep':'',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'charset':'utf-8',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':False,
        'checkunknownentities': False,
        'contenttype':'text/xml ',
        'decimaal':'.',
        'defaultBOTSIDroot':'ROOT',
        'envelope':'',
        'escape':'',
        'field_sep':'',
        'forcequote':0, #csv only
        'indented':False,               #False:  output is one string (no cr/lf); True: output is indented/human readable
        'lengthnumericbare':False,
        'merge':False,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':"",
        'record_sep':"",
        'record_tag_sep':"",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':'',
        'skip_char':'',
        'stripfield_sep':False,
        'triad':'',
        }
class tradacoms(Grammar):
    defaultsyntax = {
        'add_crlfafterrecord_sep':'\n',
        'acceptspaceinnumfield':True,   #only really used in fixed formats
        'charset':'us-ascii',
        'checkcharsetin':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcharsetout':'strict', #strict, ignore or botsreplace (replace with char as set in bots.ini).
        'checkcollision':True,
        'checkunknownentities': True,
        'contenttype':'application/text',
        'decimaal':'.',
        'envelope':'tradacoms',
        'escape':'?',
        'field_sep':'+',
        'forcequote':0, #csv only
        'indented':False,               #False:  output is one string (no cr/lf); True:  output is indented/human readable
        'lengthnumericbare':True,
        'merge':False,
        'noBOTSID':False,
        'pass_all':False,
        'quote_char':'',
        'record_sep':"'",
        'record_tag_sep':"=",    #Tradacoms/GTDI
        'reserve':'',
        'sfield_sep':':',
        'skip_char':'\r\n',
        'stripfield_sep':True,
        'triad':'',
        'wrap_length':0,     #for producing wrapped format, where a file consists of fixed length lex_records ending with crr/lf. Often seen in mainframe, as400
        'STX.STDS1':'ANA',
        'STX.STDS2':'1',
        'STX.FROM.02':'',
        'STX.UNTO.02':'',
        'STX.APRF':'',
        'STX.PRCD':'',
        }
    formatconvert = {
        'X':'A',
        '9':'R',
        '9V9':'I',
        }
class database(jsonnocheck):
    #20120101 depreciated. use 'db'
    pass
