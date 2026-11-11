"""kitlib.hubsite.cli.pages: the pages and skill-edit verbs of tools/hub.py.

  hub.py pages [--repo R]                           `ID v2 - [repo] title` for each published page
  hub.py pages publish ID --title T --html-file F [--repo R] [--summary S] [--refs ID ...]
        [--kind K] [--folder F] [--from NAME]
        [--if-version V]                             `PUBLISHED ID`; F is a UTF-8 HTML file the
                                                     hub's page check (kitlib.hubvalidate.pagecheck)
                                                     passes, checked here before it is sent;
                                                     over a page already published, --if-version
                                                     is its version (docs/hub-skill-pages.md
                                                     "pages/<id> and page_html/<id>")
  hub.py pages show ID [--out F]                    the page's HTML, or with --out F written to F
                                                     as UTF-8 and `WROTE F` printed
  hub.py pages place ID [--kind K] [--folder F] [--repo R] --if-version V
                                                    `PLACED ID v3`: sets the card's kind, folder
                                                     and repo (at least one); the body is
                                                     unchanged

  hub.py skill-edit [--status S]                    `E-1 v2 submitted skill NAME` for each of the
                                                     owner's edits of a kit skill (the owner
                                                     writes them on the hub page only;
                                                     docs/hub-skill-pages.md "skill_edits/E-NNN")
  hub.py skill-edit show E-1 [--out F]              its status, skill and base blob; --out F
                                                     writes the owner's text to F byte for byte
  hub.py skill-edit done E-1 --branch B [--checkout DIR] --if-version V    `COMMITTED E-1`
                                                     posts B and the blob of the skill's SKILL.md
                                                     on B in DIR; the hub refuses a blob that is
                                                     not the owner's text
  hub.py skill-edit fail E-1 --why W [--at REV] [--checkout DIR] --if-version V    `FAILED E-1`, or
                                                     `SUPERSEDED E-1` when the blob at REV is not
                                                     the one the owner edited
"""
from kitlib.hubclient import client
from kitlib.hubclient import errors as huberrors
from kitlib.hubsite import skilledits
from kitlib.hubsite.cli import common
from kitlib.hubvalidate import pagecheck


def pages(a, say, stop=None):
    """No delete: deleting a page is the owner's, on the hub page only."""
    hub = common.hub(a)
    if a.sub == 'publish':
        html = common.text_of(a.html_file, 'html')
        why = pagecheck.check(html)
        if why:
            raise huberrors.ServeError('the page check refuses %s: %s' % (a.html_file, why))
        got = hub.post('/pages', client.AGENT, common.body(
            id=a.id, title=a.title, html=html, repo=getattr(a, 'repo', None), summary=a.summary,
            refs=a.refs, kind=a.kind, folder=a.folder, if_version=a.if_version,
            **{'from': a.who}))
        return common.wrote(a, say, got, 'PUBLISHED %s' % got.get('id'))
    if a.sub == 'place':
        got = hub.post(common.one('pages', a) + '/place', client.AGENT, common.body(
            kind=a.kind, folder=a.folder, repo=getattr(a, 'repo', None),
            if_version=a.if_version))
        return common.wrote(a, say, got, 'PLACED %s v%s' % (got.get('id'), got.get('version')))
    if a.sub == 'show':
        got = hub.page(a.id)
        html = got.get('html') if isinstance(got.get('html'), str) else ''
        if a.out:
            try:
                with open(a.out, 'w', encoding='utf-8', newline='') as f:
                    f.write(html)
            except OSError as e:
                raise huberrors.ServeError('cannot write %s: %s' % (a.out, type(e).__name__))
            return common.show(a, say, {'ok': True, 'id': got.get('id'),
                                        'version': got.get('version'), 'out': a.out},
                               ['WROTE %s' % a.out])
        if getattr(a, 'json', False):
            return common.show(a, say, got, [])
        say(html)           # a character the console cannot show: its escape, by `say`
        return client.OK
    return common.listed(a, say, 'pages', version=True)


def blob_at(checkout, rev, skill):
    """The blob id of the skill's source at `rev` in `checkout` (git rev-parse, a read)."""
    from kitlib.git import cmd
    path = skilledits.source_path(skill)
    out, why = cmd.run_text(['rev-parse', '--verify', '--quiet', '%s:%s' % (rev, path)],
                            checkout)
    if not skilledits.is_blob(out):
        raise huberrors.ServeError('cannot read %s at %s in %s: %s'
                               % (path, rev, checkout, why or 'not found'))
    return out


def skill_edit(a, say, stop=None):
    """The owner's edits are the owner's (the hub page): an agent shows, commits and reports."""
    hub = common.hub(a)
    if a.sub in ('done', 'fail'):
        d = hub.get(common.one('skill_edits', a)).get('item') or {}
        if a.sub == 'done':
            body = {'branch': a.branch, 'if_version': a.if_version,
                    'blob': blob_at(a.checkout, a.branch, d.get('skill'))}
        else:
            body = common.body(why=a.why, if_version=a.if_version)
            if a.at:
                body['current_blob'] = blob_at(a.checkout, a.at, d.get('skill'))
        got = hub.post(common.one('skill_edits', a) + '/' + a.sub, client.AGENT, body)
        return common.wrote(a, say, got, '%s %s' % (
            'COMMITTED' if a.sub == 'done' else str(got.get('status') or 'failed').upper(),
            got.get('id')))
    if a.sub == 'show':
        got = hub.get(common.one('skill_edits', a))
        d = got.get('item') or {}
        text = d.get('text') if isinstance(d.get('text'), str) else ''
        if a.out:
            try:
                with open(a.out, 'w', encoding='utf-8', newline='') as f:
                    f.write(text)
            except OSError as e:
                raise huberrors.ServeError('cannot write %s: %s' % (a.out, type(e).__name__))
            return common.show(a, say, {'ok': True, 'id': d.get('id'),
                                        'version': d.get('version'), 'out': a.out},
                               ['WROTE %s' % a.out])
        return common.show(a, say, got, ['%s v%s %s skill %s base %s' % (
            d.get('id'), d.get('version'), d.get('status'), d.get('skill'), d.get('base_blob'))]
            + ['%s: %s' % (k, d[k]) for k in ('branch', 'blob', 'source_blob', 'why') if d.get(k)])
    got = hub.get('/skill_edits', query={'status': getattr(a, 'status', None)})
    return common.show(a, say, got, ['%s v%s %s skill %s' % (d.get('id'), d.get('version'),
                                                             d.get('status'), d.get('skill'))
                                     for d in got.get('items') or []])
