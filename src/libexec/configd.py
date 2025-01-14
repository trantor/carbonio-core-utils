#!/opt/zextras/bin/zmpython

# SPDX-FileCopyrightText: 2022 Synacor, Inc.
# SPDX-FileCopyrightText: 2022 Zextras <https://www.zextras.com>
#
# SPDX-License-Identifier: GPL-2.0-only

#set tempdir
import tempfile
tempfile.tempdir="/opt/zextras/data/tmp"

import sys
import os
import signal
import socket
import threading
import traceback

import conf
from org.apache.logging.log4j.core.config import Configurator
import state
import listener
from ldap import Ldap
from logmsg import *

if (os.geteuid() == 0):
	Log.logMsg(0, "Error: must not be run as root user.");

# Removed this to speed up startup at the cost of some slowdown in the first fetch
# Calling a Provisioning command here speeds up future runs
# th = threading.Thread(target=commands.getserver,name="preconnect")
# th.start()

# Signal handling workaround.  Directly affects the time required for a rewrite request to process
sleepinterval = 1.0

def catch_signal(signum, fr):
	Log.logMsg(4, "Received signal %d" % (signum,));
	if signum in (signal.SIGCHLD, signal.SIGHUP, signal.SIGUSR2, signal.SIGALRM):
		myState.sleepTimer = 0
		return
	Log.logMsg(3, "Shutting down. Received signal %d" % (signum,));
	sys.exit(0)

signal.signal(signal.SIGUSR2, catch_signal)
signal.signal(signal.SIGHUP, catch_signal)
signal.signal(signal.SIGINT, catch_signal)
signal.signal(signal.SIGCHLD, catch_signal)
signal.signal(signal.SIGTERM, catch_signal)

# Can't trap SIGQUIT, SIGKILL in jython?
# signal.signal(signal.SIGQUIT, catch_signal)
# signal.signal(signal.SIGKILL, catch_signal)

def watchdog():
	if (not myConfig.watchdog) or myState.firstRun:
		return
	Log.logMsg(4, "Watchdog enabled checking services");

	for service in sorted(myState.curServices()):
		prevstatus = myState.prevServices(service)
		curstatus = myState.processIsRunning(service) and "running" or "stopped"
		if prevstatus is not None and curstatus != prevstatus:
			Log.logMsg(1, "Service status change: %s %s changed from %s to %s" % (myConfig.hostname, service, prevstatus, curstatus))
		myState.prevServices(service, curstatus)

		# services need to be seen running at least once before eligible for watchdog restart
		if myState.getWatchdog(service) is None:
			Log.logMsg(1, "Tracking service %s " % (service))
		if curstatus == "running" and myState.getWatchdog(service) is None:
			Log.logMsg(3, "Watchdog: service %s now available for watchdog." % (service,))
			myState.watchdogProcess[service] = True

	if myConfig.wd_all:
		for service in sorted(myState.curServices()):
			if myState.getWatchdog(service) is None:
				continue
			if (myState.serverconfig.getServices(service) and myState.processIsNotRunning(service)):
				Log.logMsg(2, "Watchdog: adding %s to restart list" % (service,));
				myState.curRestarts(service, -1)
				
	else:
		for service in sorted(myConfig.wd_list):
			Log.logMsg(4, "Watchdog: checking service %s" % (service,));
			if myState.getWatchdog(service) is None:
				Log.logMsg(3, "Watchdog: skipping service %s. Service not yet available for restarts." % (service,));
				continue
			if (myState.serverconfig.getServices(service) and myState.processIsNotRunning(service)):
				Log.logMsg(2, "Watchdog: adding %s to restart list" % (service,));
				myState.curRestarts(service, -1)
			else:
				Log.logMsg(3, "Watchdog: service %s status is OK." % (service,));

def request_listener():
	if state.State.mState.serverconfig["zimbraIPMode"] == "ipv4":
		listener_params = ("127.0.0.1",int(state.State.mState.localconfig["zmconfigd_listen_port"]))
		try:
			server = listener.ThreadedStreamServer(listener_params, listener.ThreadedRequestHandler)
		except socket.error, e:
			Log.logMsg (1, "Error creating listener socket on port %s: %s" % (state.State.mState.localconfig["zmconfigd_listen_port"],str(e)))
			if contact_service("STATUS"):
				Log.logMsg (0, "Can't create listener socket: %s" % str(e))
			else:
				Log.logMsg (0, "zmconfigd service already running, exiting")
	else:
		listener_params = ("::1",int(state.State.mState.localconfig["zmconfigd_listen_port"]))
		try:
			server = listener.ThreadedStreamServerIPv6(listener_params, listener.ThreadedRequestHandler)
		except socket.error, e:
			Log.logMsg (1, "Error creating listener socket on port %s: %s" % (state.State.mState.localconfig["zmconfigd_listen_port"],str(e)))
			if contact_service("STATUS"):
				Log.logMsg (0, "Can't create listener socket: %s" % str(e))
			else:
				Log.logMsg (0, "zmconfigd service already running, exiting")

	server_thread = threading.Thread(target=server.serve_forever, name="listener")
	server_thread.setDaemon(True)
	server_thread.start()
	Log.logMsg(4, "Socket listener running as %s" % server_thread.getName())

def contact_service(command, args=None):
	listener_params = ("localhost",int(state.State.mState.localconfig["zmconfigd_listen_port"]))
	if state.State.mState.serverconfig["zimbraIPMode"] == "ipv4":
		sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	else:
		sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
	message = "%s " % command
	if args:
		message += ' '.join(args)
	Log.logMsg(4, "Requesting %s" % message)
	try:
		sock.connect(listener_params)
		sock.send(message)
		response = sock.recv(2048)
		if re.match("ERROR", response):
			Log.logMsg(1, "Service returned %s" % response)
			sock.close()
			return True
		else:
			Log.logMsg(4, "Service returned %s" % response)
			sock.close()
			return False
	except socket.error, e:
		Log.logMsg(2, "Service unavailable (%s)" % e)
		return True

t0 = time.clock()
myState = state.State()
state.State.mState = myState
myConfig = conf.Config()
conf.Config.mConfig = myConfig;
myState.getLocalConfig(myConfig)
Configurator.initialize(None, "/opt/zextras/conf/zmconfigd.log4j.properties")
Log.initLogging(myConfig)
Ldap.initLdap(myConfig)

Log.logMsg(1, "%s started on %s with loglevel=%d pid=%d" % (myConfig.progname, myConfig.hostname, myConfig.loglevel, os.getpid()))

# if forced, check for a running daemon.  If there's not one, just run once to maintain legacy behavior
if len(sys.argv) > 1:
	if contact_service("REWRITE", sys.argv[1:]):
		Log.logMsg(3, "Processing forced rewrites as standalone process")
		for arg in sys.argv[1:]:
			myState.forced += 1
			Log.logMsg(3, "Adding %s to forced configs" % arg)
			myState.forcedconfig[arg] = arg
	else:
		dt = time.clock()-t0
		Log.logMsg(4, "%s completed in %.2f seconds" % (myConfig.progname,dt));
		sys.exit(0)

# Removed this to speed up startup at the cost of some slowdown in the first fetch
# We don't really care about this thread, but should make sure it's done
# th.join()

while True:

	Log.logMsg (4, "Found %d threads" % threading.activeCount())

	for th in threading.enumerate():
		Log.logMsg(4, "Active Thread %s found" % th.getName())
		if (th.getName() != "listener" and th.getName() != "MainThread" and th.getName() != "Thread" and th.getName != "SIGUSR2 handler"):
			Log.logMsg(4, "Attempting to join() %s" % th.getName())
			th.join(5);
			if (th.isAlive()):
				Log.logMsg(1, "join() %s FAILED" % th.getName())
				Log.logMsg(1, "Hung threads detected (%d total), exiting" % threading.activeCount());
				sys.exit(1)
	
	t1 = time.clock()

	try:
		# read all the configs 
		myState.getAllConfigs(myConfig)
	except Exception, e:
		[Log.logMsg(1,t) for t in traceback.format_tb(sys.exc_info()[2])]
		if myState.forced:
			Log.logMsg(0, "Key lookup failed.")
		Log.logMsg(1, "Sleeping...Key lookup failed (%s)" % (e,))
		time.sleep(60)
		continue

	try:
		# read zmconfigd config
		myState.getMtaConfig(myConfig.configFile)

		# watchdog restarts apps if they are not running
		watchdog()

		# check for config changes
		myState.compareKeys()

		Log.logMsg (5, "LOCK myState.lAction requested")
		myState.lAction.acquire()
		Log.logMsg (5, "LOCK myState.lAction acquired")
		myState.compileActions()
		myState.requestedconfig = {}
		myState.doConfigRewrites()
		myState.lAction.notifyAll()
		myState.lAction.release()
		Log.logMsg (5, "LOCK myState.lAction released")

		# executes rewrites/postconf/restarts
		if myConfig.restartconfig:
			myState.doRestarts()
	except Exception, e:
		[Log.logMsg(1,t) for t in traceback.format_tb(sys.exc_info()[2])]
		if myState.forced and myState.forced < 100:
			Log.logMsg(0, "Configuration inconsistency detected (%s)" % (e,))
		Log.logMsg(1, "Sleeping...Configuration inconsistency detected (%s)" % (e,));
		time.sleep(60)
		continue

	if myState.forced:
		break

	# start the listener after we have the lock, or an early request can cause problems
	# start the listener after the rewrites, so the start script doesn't return before they're complete
	if myState.firstRun and not myState.forced:
		request_listener()

	Log.logMsg (5, "LOCK myState.lAction released")
	myState.firstRun = False
	lt = time.clock()-t1
	Log.logMsg(4, "Loop completed in %.2f seconds" % (lt,));
	Log.logMsg(4, "Sleeping for %d." % (myConfig.interval,));

	# Jython won't wake up from time.sleep() when a signal is received and caught.
	# Uncaught signals seem to cause the JVM to exit
	myState.sleepTimer = myConfig.interval
	Log.logMsg (5, "Sleeping for %d" % myState.sleepTimer)
	while myState.sleepTimer > 0:
		time.sleep(sleepinterval) 
		myState.sleepTimer -= sleepinterval
	Log.logMsg (5, "Waking up")
	
dt = time.clock()-t0
Log.logMsg(5, "%s completed in %.2f seconds" % (myConfig.progname,dt));
sys.exit(0)

