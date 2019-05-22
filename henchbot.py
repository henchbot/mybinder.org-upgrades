from yaml import safe_load as load
import requests
import subprocess
import os
import shutil

REPO_API = 'https://api.github.com/repos/jupyterhub/mybinder.org-deploy/'
TOKEN = os.environ.get('HENCHBOT_TOKEN')


class henchBotMyBinder:
    def __init__(self):
        self.get_new_commits()


    def update_repos(self, repos):
        my_binder_prs = requests.get(REPO_API + 'pulls?state=open')
        henchbot_prs = [x for x in my_binder_prs.json() if x['user']['login'] == 'henchbot']

        if len(henchbot_prs) == 0:
             self.remove_fork()

        for repo in repos:
            if self.commit_info[repo]['live'] != self.commit_info[repo]['latest']:
                existing_pr = self.check_existing_prs(henchbot_prs, repo)
                if existing_pr == None:
                    continue
                self.upgrade_repo_commit(existing_pr, repo)


    def check_existing_prs(self, henchbot_prs, repo):
        if not henchbot_prs:
            return False
        else:
            for pr in henchbot_prs:
                if pr['title'].split(':')[0].strip().lower() == repo:
                    pr_latest = pr['title'].split('...')[-1].strip()
                    if pr_latest == self.commit_info[repo]['latest']:
                        return None
                    return {'number': pr['number'], 'prev_latest': pr_latest}
            return False


    def remove_fork(self):
        res = requests.delete(
            'https://api.github.com/repos/henchbot/mybinder.org-deploy',
            headers={
                'Authorization': 'token {}'.format(TOKEN)})


    def make_fork(self):
        res = requests.post(REPO_API + 'forks',
            headers={'Authorization': 'token {}'.format(TOKEN)})

    def clone_fork(self):
        subprocess.check_call(
            ['git', 'clone', 'https://github.com/henchbot/mybinder.org-deploy'])

    def checkout_branch(self, existing_pr, repo):
        if not existing_pr:
            subprocess.check_call(
                ['git', 'checkout', '-b', repo+'_bump'])
        else:
            subprocess.check_call(
                ['git', 'checkout', repo+'_bump'])      


    def edit_repo2docker_files(self, upgrade, existing_pr):
        with open('mybinder/values.yaml', 'r') as f:
            values_yaml = f.read()

        if not existing_pr:
            updated_yaml = values_yaml.replace(
                "jupyter/repo2docker:{}".format(
                    self.commit_info[upgrade]['live']),
                "jupyter/repo2docker:{}".format(
                    self.commit_info[upgrade]['latest']))
        else:
            updated_yaml = values_yaml.replace(
                "jupyter/repo2docker:{}".format(
                    existing_pr['prev_latest']),
                "jupyter/repo2docker:{}".format(
                    self.commit_info[upgrade]['latest']))   

        fname = 'mybinder/values.yaml'
        with open(fname, 'w') as f:
            f.write(updated_yaml)

        return [fname]


    def edit_binderhub_files(self, upgrade, existing_pr):
        with open('mybinder/requirements.yaml', 'r') as f:
            requirements_yaml = f.read()

        if not existing_pr:
            updated_yaml = requirements_yaml.replace(
                "version: 0.2.0-{}".format(self.commit_info[upgrade]['live']),
                "version: 0.2.0-{}".format(self.commit_info[upgrade]['latest']))
        else:
            updated_yaml = requirements_yaml.replace(
                "version: 0.2.0-{}".format(existing_pr['prev_latest']),
                "version: 0.2.0-{}".format(self.commit_info[upgrade]['latest']))    

        fname = 'mybinder/requirements.yaml'
        with open(fname, 'w') as f:
            f.write(updated_yaml)

        return [fname]


    def edit_files(self, upgrade, existing_pr):

        if upgrade == 'repo2docker':
            return self.edit_repo2docker_files(upgrade, existing_pr)

        elif upgrade == 'binderhub':
            return self.edit_binderhub_files(upgrade, existing_pr)


    def add_commit_push(self, files_changed, repo):
        for f in files_changed:
            subprocess.check_call(['git', 'add', f])

        if repo == 'repo2docker':
            commit_message = 'repo2docker: https://github.com/jupyter/repo2docker/compare/{}...{}'.format(
                self.commit_info['repo2docker']['live'], self.commit_info['repo2docker']['latest'])
        elif repo == 'binderhub':
            commit_message = 'binderhub: https://github.com/jupyterhub/binderhub/compare/{}...{}'.format(
                self.commit_info['binderhub']['live'], self.commit_info['binderhub']['latest'])

        subprocess.check_call(['git', 'config', 'user.name', 'henchbot'])
        subprocess.check_call(['git', 'config', 'user.email', 'henchbot.github@gmail.com'])
        subprocess.check_call(['git', 'commit', '-m', commit_message])
        subprocess.check_call(['git', 'push', 'https://henchbot:{}@github.com/henchbot/mybinder.org-deploy'.format(TOKEN), repo+'_bump'])


    def upgrade_repo_commit(self, existing_pr, repo):
        if not existing_pr:
            self.make_fork()
        self.clone_fork()

        os.chdir('mybinder.org-deploy')
        self.checkout_branch(existing_pr, repo)
        files_changed = self.edit_files(repo, existing_pr)
        self.add_commit_push(files_changed, repo)
        os.chdir('..')
        shutil.rmtree('mybinder.org-deploy')

        self.create_update_pr(repo, existing_pr)


    def make_pr_body(self, repo):
        if repo == 'repo2docker':
            body = '''
            This is a repo2docker version bump. See the link below for a diff of new changes:

            https://github.com/jupyter/repo2docker/compare/{}...{}
            '''.format(self.commit_info['repo2docker']['live'], self.commit_info['repo2docker']['latest']).strip()
        elif repo == 'binderhub':
            body = '''
            This is a binderhub version bump. See the link below for a diff of new changes:

            https://github.com/jupyterhub/binderhub/compare/{}...{}
            '''.format(self.commit_info['binderhub']['live'], self.commit_info['binderhub']['latest']).strip()
        return body


    def create_update_pr(self, repo, existing_pr):
        body = self.make_pr_body(repo)

        pr = {
            'title': '{}: {}...{}'.format(repo,
                                          self.commit_info[repo]['live'],
                                          self.commit_info[repo]['latest']),
            'body': body,
            'base': 'master',
            'head': 'henchbot:{}_bump'.format(repo)}

        if existing_pr:
            res = requests.patch(REPO_API + 'pulls/{}'.format(existing_pr['number']),
                headers={'Authorization': 'token {}'.format(TOKEN)}, json=pr)

        else:
            res = requests.post(REPO_API + 'pulls',
                headers={'Authorization': 'token {}'.format(TOKEN)}, json=pr)


    def get_binderhub_live(self):
        # Load master requirements
        url_requirements = "https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/requirements.yaml"
        requirements = load(requests.get(url_requirements).text)
        binderhub_dep = [ii for ii in requirements[
            'dependencies'] if ii['name'] == 'binderhub'][0]
        bhub_live = binderhub_dep['version'].split('-')[-1]
        self.commit_info['binderhub']['live'] = bhub_live


    def get_jupyterhub_live(self):
        url_binderhub_requirements = "https://raw.githubusercontent.com/jupyterhub/binderhub/{}/helm-chart/binderhub/requirements.yaml".format(
            self.commit_info['binderhub']['live'])
        requirements = load(requests.get(url_binderhub_requirements).text)
        jupyterhub_dep = [ii for ii in requirements[
            'dependencies'] if ii['name'] == 'jupyterhub'][0]
        jhub_live = jupyterhub_dep['version'].split('-')[-1]
        self.commit_info['jupyterhub']['live'] = jhub_live


    def get_repo2docker_live(self):
        # Load master repo2docker
        url_helm_chart = "https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/values.yaml"
        helm_chart = requests.get(url_helm_chart)
        helm_chart = load(helm_chart.text)
        r2d_live = helm_chart['binderhub']['config'][
            'BinderHub']['build_image'].split(':')[-1]
        self.commit_info['repo2docker']['live'] = r2d_live


    def get_repo2docker_latest(self):
        # Load latest r2d commit from dockerhub
        url = "https://hub.docker.com/v2/repositories/jupyter/repo2docker/tags/"
        resp = requests.get(url)
        r2d_master = resp.json()['results'][0]['name']
        self.commit_info['repo2docker']['latest'] = r2d_master
        print('repo2docker', self.commit_info['repo2docker']['live'], self.commit_info['repo2docker']['latest'])


    def get_bhub_jhub_latest(self):
        # Load latest binderhub and jupyterhub commits
        url_helm_chart = 'https://raw.githubusercontent.com/jupyterhub/helm-chart/gh-pages/index.yaml'
        helm_chart_yaml = load(requests.get(url_helm_chart).text)

        for repo in ['binderhub', 'jupyterhub']:
            updates_sorted = sorted(
                helm_chart_yaml['entries'][repo],
                key=lambda k: k['created'])
            self.commit_info[repo]['latest'] = updates_sorted[-1]['version'].split('-')[-1]
            print(repo, self.commit_info[repo]['live'], self.commit_info[repo]['latest'])


    def get_new_commits(self):
        self.commit_info = {'binderhub': {},
                       'repo2docker': {},
                       'jupyterhub': {}}

        print('Fetching the SHA for live BinderHub and repo2docker...')
        self.get_binderhub_live()
        self.get_jupyterhub_live()
        self.get_repo2docker_live()

        print('Fetching latest commit SHA for BinderHub and repo2docker...')
        self.get_repo2docker_latest()
        self.get_bhub_jhub_latest()


if __name__ == '__main__':
    hb = henchBotMyBinder()
    hb.update_repos(['repo2docker', 'binderhub'])