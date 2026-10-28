"""Prepare a PRIVATE, trial-only WSGI preview inside the hosting owner's account.

No hosting API, password, billing, database reset, or public verification endpoint.
A human must create the free web app, set its virtualenv/WSGI entrypoint and Reload.
"""
import argparse
import os
from pathlib import Path
import re

DOMAIN = re.compile(r'([a-z0-9][a-z0-9_-]{0,62})(?:\.eu)?\.pythonanywhere\.com')


def prepare_preview(project, domain, *, home=None):
    project = Path(project).resolve()
    home = Path(home or Path.home()).resolve()
    match = DOMAIN.fullmatch(domain)
    if not match or match.group(1) != home.name:
        raise ValueError('Use the exact free domain shown in YOUR PythonAnywhere Web tab.')
    if project == home or not project.is_relative_to(home):
        raise ValueError('Use a project directory inside your own home directory.')
    if not (project/'server'/'wsgi.py').is_file():
        raise ValueError('Missing WSGI support; get the documented project version first.')
    data = home/'shamaa-preview-data'
    env = project/'.env'
    if env.is_symlink() or data.is_symlink():
        raise ValueError('Private configuration/data must not be symbolic links.')
    values = {
        'SHAMAA_ENV':'development',
        'SHAMAA_DB':str(data/'marketplace.sqlite3'),
        'SHAMAA_DATA_DIR':str(data),
        'SHAMAA_SQLITE_JOURNAL_MODE':'DELETE',
        'SHAMAA_PUBLIC_URL':'https://'+domain,
        'SHAMAA_ALLOWED_HOSTS':domain,
        'SHAMAA_DEMO_DATA':'1',
        'SHAMAA_EMAIL_MODE':'file',
    }
    if env.exists():
        from dotenv import dotenv_values
        existing = dotenv_values(env)
        if any(existing.get(k) != v for k,v in values.items()):
            raise ValueError('Existing .env differs. Nothing overwritten: review it privately first.')
    else:
        if data.exists() and any(data.iterdir()):
            raise ValueError('Existing data not adopted or reset. Review it privately first.')
        # This is dotenv syntax, NOT shell commands. Never source an untrusted .env.
        from dotenv import set_key
        fd = os.open(env,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd)
        for key,value in values.items():
            set_key(env,key,value,quote_mode='always',encoding='utf-8')
    env.chmod(0o600)
    data.mkdir(exist_ok=True,mode=0o700)
    data.chmod(0o700)
    text = (
        '# SHAM\u2019A trial preview; paste into the Web tab WSGI file.\n'
        'import sys\n'
        f'project = {str(project)!r}\n'
        'if project not in sys.path:\n'
        '    sys.path.insert(0, project)\n'
        'from server.wsgi import create_wsgi_app\n'
        'application = create_wsgi_app()\n'
    )
    launcher = data/'pythonanywhere_wsgi.py'
    if launcher.is_symlink():
        raise ValueError('Refusing a symbolic-link WSGI launcher.')
    fd = os.open(launcher,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        stream.write(text)
    launcher.chmod(0o600)
    return {'domain':domain,'url':values['SHAMAA_PUBLIC_URL'],'data_dir':data,
            'virtualenv':project/'.venv','launcher':launcher,'wsgi_text':text}


def main():
    parser = argparse.ArgumentParser(description='Prepare a private FREE TRIAL preview; does not deploy or create an account.')
    parser.add_argument('--domain',required=True,help='Exact domain from your PythonAnywhere Web tab')
    args = parser.parse_args()
    try:
        result = prepare_preview(Path(__file__).resolve().parent.parent,args.domain)
    except (ValueError,OSError) as error:
        raise SystemExit(str(error)) from None
    print('Prepared preview configuration only. No remote account/deployment was created.')
    print('Set Web > Virtualenv to:',result['virtualenv'])
    print('The data directory is private and separate from the Git checkout:',result['data_dir'])
    print('Back up the existing Web-tab WSGI file privately before replacing it.')
    print('Paste the following into that WSGI file, save, enable Force HTTPS and Reload:')
    print(result['wsgi_text'])
    print('EXPECTED URL AFTER RELOAD (not yet verified):',result['url'])
    print('TRIAL ONLY: file email is not sent; orders are test records. Keep external backups and renew the free web app.')


if __name__=='__main__':
    main()
