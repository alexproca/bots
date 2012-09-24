#!/usr/bin/env python
import sys
import os
import xmlrpclib
import socket
import botsinit
import botsglobal

JOBQUEUEMESSAGE2TXT = {
    0: u'OK, job is added to queue',
    1: u'Error, job not to jobqueue. Can not contact jobqueue-server',
    4: u'Duplicate job, not added.',
    }


def send_job_to_jobqueue(task_args,priority=5):
    ''' adds a new job to the bots-jobqueueserver.
        is a xmlrpc client.
        Import this function in eg views.py.
        Received return codes  from jobqueueserver:
        0 = OK, job added to job queue.
        4 = job is a duplicate of job already in the queue
    '''
    try:
        remote_server = xmlrpclib.ServerProxy('http://localhost:' + str(botsglobal.ini.getint('jobqueue','port',6000)))
        return remote_server.addjob(task_args,priority)
    except socket.error,msg:
        print 'socket.error',msg
        return 1    #jobqueueserver server not active


def start():
    #NOTE: bots directory should always be on PYTHONPATH - otherwise it will not start.
    #***command line arguments**************************
    usage = '''
    This is "%(name)s" version %(version)s, part of Bots open source edi translator (http://bots.sourceforge.net).
    Places a job in the bots jobqueue. Bots jobqueue takes care of correct processing of jobs.
    
    Usage:
        %(name)s  -c<directory> [-p<priority>] program [parameters]
    Options:
        -c<directory>   directory for configuration files (default: config).
        -p<priority>    priority of job, 1-9 (default: 5, highest priority is 9).
    Example of usage:
        %(name)s -cconfig -p5 python2.7 /usr/local/bin/bots-engine.py
        
    '''%{'name':os.path.basename(sys.argv[0]),'version':botsglobal.version}
    configdir = 'config'    #default value
    priority = 5            #default value
    task_args = []
    for arg in sys.argv[1:]:
        if arg.startswith('-c'):
            configdir = arg[2:]
            if not configdir:
                print 'Error: configuration directory indicated, but no directory name.'
                sys.exit(1)
        elif arg.startswith('-p'):
            try:
                priority =  int(arg[2:])
            except:
                print 'Error: priority should be numeric (1=highest, 9=lowest).'
                sys.exit(1)
        elif arg in ["?", "/?",'-h', '--help']:
            print usage
            sys.exit(0)
        else:
            task_args.append(arg)
    task_args.append(configdir)
    #***end handling command line arguments**************************
    botsinit.generalinit(configdir)         #needed to read config
    if not botsglobal.ini.getboolean('jobqueue','enabled',False):
        print 'Error: bots jobqueue cannot start; not enabled in %s/bots.ini' % configdir
        sys.exit(1)
        
    terug = send_job_to_jobqueue(task_args,priority)
    print JOBQUEUEMESSAGE2TXT[terug]
    sys.exit(terug)


if __name__ == '__main__':
    start()
