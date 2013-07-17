''' functions for reading and making plugins.
    Reading an making functions are seperate functions.
'''
import os
import sys
#~ import time
import zipfile
import zipimport
import codecs
import django
from django.core import serializers
from django.utils.translation import ugettext as _
import models
import botslib
import botsglobal

#******************************************
#* read a plugin **************************
#******************************************
@django.db.transaction.commit_on_success  #if no exception raised: commit, else rollback.
def read_index(filename):
    ''' process index file in default location. '''
    try:
        importedbotsindex,scriptname = botslib.botsimport('index')
        pluglist = importedbotsindex.plugins[:]
        if 'botsindex' in sys.modules:
            del sys.modules['botsindex']
    except:
        txt = botslib.txtexc()
        raise botslib.PluginError(_(u'Error in configuration index file. Nothing is written. Error:\n%(txt)s'),{'txt':txt})
    else:
        botsglobal.logger.info(_(u'Configuration index file is OK.'))
        botsglobal.logger.info(_(u'Start writing to database.'))

    #write content of index file to the bots database
    try:
        read_index2database(pluglist)
    except:
        txt = botslib.txtexc()
        raise botslib.PluginError(_(u'Error writing configuration index to database. Nothing is written. Error:\n%(txt)s'),{'txt':txt})
    else:
        botsglobal.logger.info(_(u'Writing to database is OK.'))


@django.db.transaction.commit_on_success  #if no exception raised: commit, else rollback.
def read_plugin(pathzipfile):
    ''' process uploaded plugin. '''
    #test if valid zipfile
    if not zipfile.is_zipfile(pathzipfile):
        raise botslib.PluginError(_(u'Plugin is not a valid file.'))

    #read index file
    try:
        myzipimport = zipimport.zipimporter(pathzipfile)
        importedbotsindex = myzipimport.load_module('botsindex')
        pluglist = importedbotsindex.plugins[:]
        if 'botsindex' in sys.modules:
            del sys.modules['botsindex']
    except:
        txt = botslib.txtexc()
        raise botslib.PluginError(_(u'Error in plugin. Nothing is written. Error:\n%(txt)s'),{'txt':txt})
    else:
        botsglobal.logger.info(_(u'Plugin is OK.'))
        botsglobal.logger.info(_(u'Start writing to database.'))

    #write content of index file to the bots database
    try:
        read_index2database(pluglist)
    except:
        txt = botslib.txtexc()
        raise botslib.PluginError(_(u'Error writing plugin to database. Nothing is written. Error:\n%(txt)s'),{'txt':txt})
    else:
        botsglobal.logger.info(_(u'Writing to database is OK.'))

    #write files to the file system.
    botsglobal.logger.info(_(u'Start writing to files'))
    try:
        warnrenamed = False     #to report in GUI files have been overwritten.
        myzip = zipfile.ZipFile(pathzipfile, mode="r")
        orgtargetpath = botsglobal.ini.get('directories','botspath')
        if (orgtargetpath[-1:] in (os.path.sep, os.path.altsep) and len(os.path.splitdrive(orgtargetpath)[1]) > 1):
            orgtargetpath = orgtargetpath[:-1]
        for zipfileobject in myzip.infolist():
            if zipfileobject.filename not in ['botsindex.py','README','botssys/sqlitedb/botsdb','config/bots.ini'] and os.path.splitext(zipfileobject.filename)[1] not in ['.pyo','.pyc']:
                #~ botsglobal.logger.info(u'Filename in zip "%s".',zipfileobject.filename)
                if zipfileobject.filename[0] == '/':
                    targetpath = zipfileobject.filename[1:]
                else:
                    targetpath = zipfileobject.filename
                #convert for correct environment: repacle botssys, config, usersys in filenames
                if targetpath.startswith('usersys'):
                    targetpath = targetpath.replace('usersys',botsglobal.ini.get('directories','usersysabs'),1)
                elif targetpath.startswith('botssys'):
                    targetpath = targetpath.replace('botssys',botsglobal.ini.get('directories','botssys'),1)
                elif targetpath.startswith('config'):
                    targetpath = targetpath.replace('config',botsglobal.ini.get('directories','config'),1)
                targetpath = botslib.join(orgtargetpath, targetpath)
                #targetpath is OK now.
                botsglobal.logger.info(_(u'    Start writing file: "%(targetpath)s".'),{'targetpath':targetpath})

                if botslib.dirshouldbethere(os.path.dirname(targetpath)):
                    botsglobal.logger.info(_(u'        Create directory "%(directory)s".'),{'directory':os.path.dirname(targetpath)})
                if zipfileobject.filename[-1] == '/':    #check if this is a dir; if so continue
                    continue
                if os.path.isfile(targetpath):  #check if file already exists
                    try:    #this ***sometimes*** fails. (python25, for static/help/home.html...only there...)
                        warnrenamed = True
                    except:
                        pass
                source = myzip.read(zipfileobject.filename)
                target = open(targetpath, "wb")
                target.write(source)
                target.close()
                botsglobal.logger.info(_(u'        File written: "%(targetpath)s".'),{'targetpath':targetpath})
    except:
        txt = botslib.txtexc()
        myzip.close()
        raise botslib.PluginError(_(u'Error writing files to system. Nothing is written to database. Error:\n%(txt)s'),{'txt':txt})
    else:
        myzip.close()
        botsglobal.logger.info(_(u'Writing files to filesystem is OK.'))
        return warnrenamed

#PLUGINCOMPARELIST: for filtering and sorting the plugins.
PLUGINCOMPARELIST = ['uniek','persist','mutex','ta','filereport','report','ccodetrigger','ccode', 'channel','partner','chanpar','translate','routes','confirmrule']

def read_index2database(orgpluglist):
    #sanity checks on pluglist
    if not orgpluglist:  #list of plugins is empty: is OK. DO nothing
        return
    if not isinstance(orgpluglist,list):   #has to be a list!!
        raise botslib.PluginError(_(u'Plugins should be list of dicts. Nothing is written.'))
    for plug in orgpluglist:
        if not isinstance(plug,dict):
            raise botslib.PluginError(_(u'Plugins should be list of dicts. Nothing is written.'))
        for key in plug.keys():
            if not isinstance(key,basestring):
                raise botslib.PluginError(_(u'Key of dict is not a string: "%(plug)s". Nothing is written.'),{'plug':plug})
        if 'plugintype' not in plug:
            raise botslib.PluginError(_(u'"Plugintype" missing in: "%(plug)s". Nothing is written.'),{'plug':plug})

    #special case: compatibility with bots 1.* plugins.
    #in bots 1.*, partnergroup was in separate tabel; in bots 2.* partnergroup is in partner
    #later on, partnergroup will get filtered
    for plug in orgpluglist[:]:
        if plug['plugintype'] == 'partnergroup':
            for plugpartner in orgpluglist:
                if plugpartner['plugintype'] == 'partner' and plugpartner['idpartner'] == plug['idpartner']:
                    if 'group' in plugpartner:
                        plugpartner['group'].append(plug['idpartnergroup'])
                    else:
                        plugpartner['group'] = [plug['idpartnergroup']]
                    break

    #copy & filter orgpluglist; do plugtype specific adaptions
    pluglist = []
    for plug in orgpluglist:
        if plug['plugintype'] == 'ccode':   #add ccodetrigger. #20101223: this is NOT needed; codetrigger shoudl be in plugin.
            for seachccodetriggerplug in pluglist:
                if seachccodetriggerplug['plugintype'] == 'ccodetrigger' and seachccodetriggerplug['ccodeid'] == plug['ccodeid']:
                    break
            else:
                pluglist.append({'plugintype':'ccodetrigger','ccodeid':plug['ccodeid']})
        elif plug['plugintype'] == 'translate': #make some fields None instead of '' (translate formpartner, topartner)
            if not plug['frompartner']:
                plug['frompartner'] = None
            if not plug['topartner']:
                plug['topartner'] = None
        elif plug['plugintype'] == 'routes':
            plug['active'] = False
            if 'defer' not in plug:
                plug['defer'] = False
            else:
                if plug['defer'] is None:
                    plug['defer'] = False
        elif plug['plugintype'] == 'channel':
            #convert for correct environment: path and mpath in channels
            if 'path' in plug and plug['path'].startswith('botssys'):
                plug['path'] = plug['path'].replace('botssys',botsglobal.ini.get('directories','botssys_org'),1)
            if 'testpath' in plug and plug['testpath'].startswith('botssys'):
                plug['testpath'] = plug['testpath'].replace('botssys',botsglobal.ini.get('directories','botssys_org'),1)
        elif plug['plugintype'] == 'confirmrule':
            plug.pop('id', None)       #id is an artificial key, delete,
        elif plug['plugintype'] not in PLUGINCOMPARELIST:   #if not in PLUGINCOMPARELIST: do not use
            continue
        pluglist.append(plug)
    #sort pluglist: this is needed for relationships
    pluglist.sort(key=lambda plug: plug.get('isgroup',False),reverse=True)       #sort partners on being partnergroup or not
    pluglist.sort(key=lambda plug: PLUGINCOMPARELIST.index(plug['plugintype']))   #sort all plugs on plugintype; are partners/partenrgroups are already sorted, this will still be true in this new sort (python guarantees!)

    for plug in pluglist:
        botsglobal.logger.info(u'    Start write to database for: "%(plug)s".',{'plug':plug})
        #correction for reading partnergroups
        if plug['plugintype'] == 'partner' and plug['isgroup']:
            plug['plugintype'] = 'partnergroep'
        #remember the plugintype
        plugintype = plug['plugintype']
            
        table = django.db.models.get_model('bots',plugintype)

        #delete fields not in model for compatibility; note that 'plugintype' is also removed.
        loopdictionary = plug.keys()
        for key in loopdictionary:
            try:
                table._meta.get_field(key)
            except django.db.models.fields.FieldDoesNotExist:
                del plug[key]

        #get key(s), put in dict 'sleutel'
        pk = table._meta.pk.name
        if pk == 'id':  #'id' is the artificial key django makes, if no key is indicated. Note the django has no 'composite keys'.
            sleutel = {}
            if table._meta.unique_together:
                for key in table._meta.unique_together[0]:
                    sleutel[key] = plug.pop(key)
        else:
            sleutel = {pk:plug.pop(pk)}

        sleutelorg = sleutel.copy()     #make a copy of the original sleutel; this is needed later
        #now we have:
        #- plugintype (is removed from plug)
        #- sleutelorg: original key fields
        #- sleutel: unique key fields. mind: translate and confirmrule have empty 'sleutel'
        #- plug: rest of database fields
        #for sleutel and plug: convert names to real database names

        #get real column names for fields in plug
        loopdictionary = plug.keys()
        for fieldname in loopdictionary:
            fieldobject = table._meta.get_field_by_name(fieldname)[0]
            try:
                if fieldobject.column != fieldname:     #if name in plug is not the real field name (in database)
                    plug[fieldobject.column] = plug[fieldname]  #add new key in plug
                    del plug[fieldname]                         #delete old key in plug
            except:
                raise botslib.PluginError(_(u'No field column for: "%(fieldname)s".'),{'fieldname':fieldname})
        #get real column names for fields in sleutel; basically the same loop but now for sleutel
        loopdictionary = sleutel.keys()
        for fieldname in loopdictionary:
            fieldobject = table._meta.get_field_by_name(fieldname)[0]
            try:
                if fieldobject.column != fieldname:
                    sleutel[fieldobject.column] = sleutel[fieldname]
                    del sleutel[fieldname]
            except:
                raise botslib.PluginError(_(u'No field column for: "%(fieldname)s".'),{'fieldname':fieldname})

        #find existing entry (if exists)
        if sleutelorg:  #note that translate and confirmrule have an empty 'sleutel'
            listexistingentries = table.objects.filter(**sleutelorg)
        elif plugintype == 'translate':
            listexistingentries = table.objects.filter(fromeditype=plug['fromeditype'],
                                                        frommessagetype=plug['frommessagetype'],
                                                        alt=plug['alt'],
                                                        frompartner=plug['frompartner_id'],
                                                        topartner=plug['topartner_id'])
        elif plugintype == 'confirmrule':
            listexistingentries = table.objects.filter(confirmtype=plug['confirmtype'],
                                                        ruletype=plug['ruletype'],
                                                        negativerule=plug['negativerule'],
                                                        idroute=plug.get('idroute'),
                                                        idchannel=plug.get('idchannel_id'),
                                                        messagetype=plug.get('messagetype'),
                                                        frompartner=plug.get('frompartner_id'),
                                                        topartner=plug.get('topartner_id'))
        if listexistingentries:
            dbobject = listexistingentries[0]  #exists, so use existing db-object
        else:
            dbobject = table(**sleutel)         #create db-object
            if plugintype == 'partner':        #for partners, first the partner needs to be saved before groups can be made
                dbobject.save()
        for key,value in plug.iteritems():      #update object with attributes from plugin
            setattr(dbobject,key,value)
        dbobject.save()                     #and save the updated object.
        botsglobal.logger.info(_(u'        Write to database is OK.'))


#*********************************************
#* make a plugin (generate)*******************
#*********************************************
def make_index(cleaned_data,filename):
    ''' generate only the index file of the plugin.
        used eg for configuration change management.
    '''
    plugs = make_database2plug(cleaned_data)
    plugsasstring = make_plugs2string(plugs)
    filehandler = codecs.open(filename,'w','utf-8')
    filehandler.write(plugsasstring)
    filehandler.close()

def make_plugin(cleaned_data,filename):
    pluginzipfilehandler = zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED)

    plugs = make_database2plug(cleaned_data)
    plugsasstring = make_plugs2string(plugs)
    pluginzipfilehandler.writestr('botsindex.py',plugsasstring.encode('utf-8'))      #write index file to pluginfile
    botsglobal.logger.debug(u'    Write in index:\n %(index)s',{'index':plugsasstring})

    files4plugin = plugout_files(cleaned_data)
    for dirname, defaultdirname in files4plugin:
        pluginzipfilehandler.write(dirname,defaultdirname)
        botsglobal.logger.debug(u'    Write file "%(file)s".',{'file':defaultdirname})

    pluginzipfilehandler.close()

def make_database2plug(cleaned_data):
    ''' get all database objects, serialize these (to dict), adapt.'''
    db_objects = []
    if cleaned_data['databaseconfiguration']:
        db_objects += \
            list(models.channel.objects.all()) + \
            list(models.partner.objects.all()) + \
            list(models.chanpar.objects.all()) + \
            list(models.translate.objects.all()) +  \
            list(models.routes.objects.all()) +  \
            list(models.confirmrule.objects.all())
    if cleaned_data['umlists']:
        db_objects += \
            list(models.ccodetrigger.objects.all()) + \
            list(models.ccode.objects.all())
    if cleaned_data['databasetransactions']:
        db_objects += \
            list(models.uniek.objects.all()) + \
            list(models.mutex.objects.all()) + \
            list(models.ta.objects.all()) + \
            list(models.filereport.objects.all()) + \
            list(models.report.objects.all())
            #~ list(models.persist.objects.all()) + \       #should persist object alos be included?
    #serialize database objects
    plugs = serializers.serialize("python", db_objects)
    #adapt plugs
    for plug in plugs:
        app,tablename = plug['model'].split('.',1)
        plug['fields']['plugintype'] = tablename
        table = django.db.models.get_model(app,tablename)
        pk = table._meta.pk.name
        if pk != 'id':
            plug['fields'][pk] = plug['pk']
        #convert for correct environment: replace botssys in channels[path, mpath]
        if plug['fields']['plugintype'] == 'channel':
            if 'path' in plug['fields'] and plug['fields']['path'].startswith(botsglobal.ini.get('directories','botssys_org')):
                plug['fields']['path'] = plug['fields']['path'].replace(botsglobal.ini.get('directories','botssys_org'),'botssys',1)
            if 'testpath' in plug['fields'] and plug['fields']['testpath'].startswith(botsglobal.ini.get('directories','botssys_org')):
                plug['fields']['testpath'] = plug['fields']['testpath'].replace(botsglobal.ini.get('directories','botssys_org'),'botssys',1)
    return plugs

def make_plugs2string(plugs):
    ''' return plugs (serialized objects) as unicode strings.
    '''
    tmpbotsindex = [u'# -*- coding: utf-8 -*-',u'import datetime',"version = '%s'" % (botsglobal.version),'plugins = [']
    for plug in plugs:
        tmpbotsindex.append(plugout_database_entry_as_string(plug['fields']))
        #check confirmrule: id is non-artifical key?
    tmpbotsindex.append(u']\n')
    return '\n'.join(tmpbotsindex)

def plugout_database_entry_as_string(plugdict):
    ''' like repr() for a dict, but:
        - starts with 'plugintype'
        - other entries are sorted; this because of predictability
        - produce unicode by using str().decode(unicode_escape): bytes->unicode; converts escaped unicode-chrs to correct unicode. repr produces these.
        str().decode(): bytes->unicode
        str().encode(): unicode->bytes
    '''
    terug = u"{" + repr('plugintype') + u": " + repr(plugdict.pop('plugintype'))
    for key in sorted(plugdict):
        terug += u", " + repr(key) + u": " + repr(plugdict[key])
    terug += u'},'
    return terug

def plugout_files(cleaned_data):
    ''' gather list of files for the plugin that is generated.
    '''
    files2return = []
    usersys = botsglobal.ini.get('directories','usersysabs')
    botssys = botsglobal.ini.get('directories','botssys')
    if cleaned_data['fileconfiguration']:       #gather from usersys
        files2return.extend(plugout_files_bydir(usersys,'usersys'))
        if not cleaned_data['charset']:     #if edifact charsets are not needed: remove them (are included in default bots installation).
            charsetdirs = plugout_files_bydir(os.path.join(usersys,'charsets'),'usersys/charsets')
            for charset in charsetdirs:
                try:
                    index = files2return.index(charset)
                    files2return.pop(index)
                except ValueError:
                    pass
    if cleaned_data['config']:
        config = botsglobal.ini.get('directories','config')
        files2return.extend(plugout_files_bydir(config,'config'))
    if cleaned_data['data']:
        data = botsglobal.ini.get('directories','data')
        files2return.extend(plugout_files_bydir(data,'botssys/data'))
    if cleaned_data['database']:
        files2return.extend(plugout_files_bydir(os.path.join(botssys,'sqlitedb'),'botssys/sqlitedb.copy'))  #yeah...readign a plugin with a new database will cause a crash...do this manually...
    if cleaned_data['infiles']:
        files2return.extend(plugout_files_bydir(os.path.join(botssys,'infile'),'botssys/infile'))
    if cleaned_data['logfiles']:
        log_file = botsglobal.ini.get('directories','logging')
        files2return.extend(plugout_files_bydir(log_file,'botssys/logging'))
    return files2return

def plugout_files_bydir(dirname,defaultdirname):
    ''' gather all files from directory dirname'''
    files2return = []
    for root, dirs, files in os.walk(dirname):
        head, tail = os.path.split(root)
        #convert for correct environment: replace dirname with the default directory name
        rootinplugin = root.replace(dirname,defaultdirname,1)
        for bestand in files:
            ext = os.path.splitext(bestand)[1]
            if ext and (ext in ['.pyc','.pyo'] or bestand in ['__init__.py']):
                continue
            files2return.append([os.path.join(root,bestand),os.path.join(rootinplugin,bestand)])
    return files2return

