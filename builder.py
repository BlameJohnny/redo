import sys, os, subprocess, random
import vars, jwack, state
from helpers import log, log_, debug2, err, unlink


def _possible_do_files(t):
    yield "%s.do" % t, t, ''
    dirname,filename = os.path.split(t)
    l = filename.split('.')
    l[0] = os.path.join(dirname, l[0])
    for i in range(1,len(l)+1):
        basename = '.'.join(l[:i])
        ext = '.'.join(l[i:])
        if ext: ext = '.' + ext
        yield (os.path.join(dirname, "default%s.do" % ext),
               os.path.join(dirname, basename), ext)


def _find_do_file(t):
    for dofile,basename,ext in _possible_do_files(t):
        debug2('%s: %s ?\n' % (t, dofile))
        if os.path.exists(dofile):
            state.add_dep(t, 'm', dofile)
            return dofile,basename,ext
        else:
            state.add_dep(t, 'c', dofile)
    return None,None,None


def _preexec(t):
    td = os.environ.get('REDO_PWD', '')
    dn = os.path.dirname(t)
    os.environ['REDO_PWD'] = os.path.join(td, dn)
    os.environ['REDO_TARGET'] = os.path.basename(t)
    os.environ['REDO_DEPTH'] = vars.DEPTH + '  '
    if dn:
        os.chdir(dn)


def _nice(t):
    return os.path.normpath(os.path.join(vars.PWD, t))


def build(t):
    if (os.path.exists(t) and not state.is_generated(t)
          and not os.path.exists('%s.do' % t)):
        # an existing source file that is not marked as a generated file.
        # This step is mentioned by djb in his notes.  It turns out to be
        # important to prevent infinite recursion.  For example, a rule
        # called default.c.do could be used to try to produce hello.c,
        # which is undesirable since hello.c existed already.
        state.stamp(t)
        return  # success
    state.start(t)
    (dofile, basename, ext) = _find_do_file(t)
    if not dofile:
        err('no rule to make %r\n' % t)
        return 1
    state.stamp(dofile)
    tmpname = '%s.redo.tmp' % t
    unlink(tmpname)
    f = open(tmpname, 'w+')

    # this will run in the dofile's directory, so use only basenames here
    argv = ['sh', '-e',
            os.path.basename(dofile),
            os.path.basename(basename),  # target name (extension removed)
            ext,  # extension (if any), including leading dot
            os.path.basename(tmpname)  # randomized output file name
            ]
    if vars.VERBOSE:
        argv[1] += 'v'
    if vars.XTRACE:
        argv[1] += 'x'
    if vars.VERBOSE or vars.XTRACE:
        log_('\n')
    log('%s\n' % _nice(t))
    rv = subprocess.call(argv, preexec_fn=lambda: _preexec(t),
                         stdout=f.fileno())
    if rv==0:
        if os.path.exists(tmpname) and os.stat(tmpname).st_size:
            # there's a race condition here, but if the tmpfile disappears
            # at *this* point you deserve to get an error, because you're
            # doing something totally scary.
            os.rename(tmpname, t)
        else:
            unlink(tmpname)
        state.stamp(t)
    else:
        unlink(tmpname)
        state.unstamp(t)
    f.close()
    if rv != 0:
        err('%s: exit code %d\n' % (_nice(t),rv))
        return 1
    if vars.VERBOSE or vars.XTRACE:
        log('%s (done)\n\n' % _nice(t))


def main(targets, buildfunc):
    retcode = [0]  # a list so that it can be reassigned from done()
    if vars.SHUFFLE:
        random.shuffle(targets)

    locked = []

    def done(t, rv):
        if rv:
            #err('%s: exit code was %r\n' % (t, rv))
            retcode[0] = 1

    for i in range(len(targets)):
        t = targets[i]
        if os.path.exists('%s/all.do' % t):
            # t is a directory, but it has a default target
            targets[i] = '%s/all' % t

    # In the first cycle, we just build as much as we can without worrying
    # about any lock contention.  If someone else has it locked, we move on.
    for t in targets:
        jwack.get_token(t)
        if retcode[0] and not vars.KEEP_GOING:
            break
        lock = state.Lock(t)
        lock.trylock()
        if not lock.owned:
            if vars.DEBUG_LOCKS:
                log('%s (locked...)\n' % _nice(t))
            locked.append(t)
        else:
            jwack.start_job(t, lock,
                            lambda: buildfunc(t), lambda t,rv: done(t,rv))

    # Now we've built all the "easy" ones.  Go back and just wait on the
    # remaining ones one by one.  This is technically non-optimal; we could
    # use select.select() to wait on more than one at a time.  But it should
    # be rare enough that it doesn't matter, and the logic is easier this way.
    while locked or jwack.running():
        jwack.wait_all()
        if retcode[0] and not vars.KEEP_GOING:
            break
        if locked:
            t = locked.pop(0)
            lock = state.Lock(t)
            lock.waitlock()
            assert(lock.owned)
            if vars.DEBUG_LOCKS:
                log('%s (...unlocked!)\n' % _nice(t))
            if state.stamped(t) == None:
                err('%s: failed in another thread\n' % _nice(t))
                retcode[0] = 2
                lock.unlock()
            else:
                jwack.start_job(t, lock, 
                                lambda: buildfunc(t), lambda t,rv: done(t,rv))
    return retcode[0]