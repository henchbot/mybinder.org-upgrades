from yaml import safe_load as load
import requests
import subprocess
import os
import shutil
import time

REPO_API = 'https://api.github.com/repos/jupyterhub/mybinder.org-deploy/'
TOKEN = os.environ.get('HENCHBOT_TOKEN')


class henchBotMyBinder:
    '''
    Class for a bot that determines whether an upgrade is necessary
    for mybinder.org dependencies on repo2docker and BinderHub.
    If an upgrade is needed, it will fork the main mybinder.org repo,
    update the SHA and create a PR.
    '''
    def __init__(self):
        '''
        Start by getting the latest commits to repo2docker
        and BinderHub, and the live SHAs in mybinder.org
        '''
        self.get_new_commits()


    def update_repos(self, repos):
        '''
        Main method to check/create upgrades
        '''
        my_binder_prs = requests.get(REPO_API + 'pulls?state=open')
        henchbot_prs = [x for x in my_binder_prs.json() if x['user']['login'] == 'henchbot']
        self.check_fork_exists()

        if len(henchbot_prs) == 0 and self.fork_exists:
             self.remove_fork()

        for repo in repos:
            if self.commit_info[repo]['live'] != self.commit_info[repo]['latest']:
                existing_pr = self.check_existing_prs(henchbot_prs, repo)
                if existing_pr == None:
                    continue

                self.upgrade_repo_commit(existing_pr, repo)


    def check_existing_prs(self, henchbot_prs, repo):
        '''
        Check mybinder.org for existing henchbot PRs
        '''
        if not henchbot_prs:
            return False
        else:
            for pr in henchbot_prs:
                if repo in pr['title'].lower():
                    pr_latest = pr['title'].split('...')[-1].strip()
                    if pr_latest == self.commit_info[repo]['latest']:
                        return None
                    return {'number': pr['number'], 'prev_latest': pr_latest}
            return False


    def check_fork_exists(self):
        '''
        Check if a fork exists for henchbot already
        '''
        res = requests.get('https://api.github.com/users/henchbot/repos')
        self.fork_exists = bool([x for x in res.json() if x['name'] == 'mybinder.org-deploy'])


    def remove_fork(self):
        '''
        Remove a henchbot fork of mybinder.org
        '''
        res = requests.delete(
            'https://api.github.com/repos/henchbot/mybinder.org-deploy',
            headers={
                'Authorization': 'token {}'.format(TOKEN)})
        self.fork_exists = False
        time.sleep(5)


    def make_fork(self):
        '''
        Make a fork of the mybinder.org repo to henchbot
        '''
        res = requests.post(REPO_API + 'forks',
            headers={'Authorization': 'token {}'.format(TOKEN)})


    def clone_fork(self):
        '''
        Clone henchbot's mybinder.org fork
        '''
        subprocess.check_call(
            ['git', 'clone', 'https://github.com/henchbot/mybinder.org-deploy'])


    def delete_old_branch(self, repo):
        '''
        Delete an old branch in the henchbot fork (if it was merged)
        '''
        res = requests.get('https://api.github.com/repos/henchbot/mybinder.org-deploy/branches')
        if repo+'_bump' in [x['name'] for x in res.json()]:
            subprocess.check_call(
                ['git', 'push', '--delete', 'origin', repo+'_bump'])
            subprocess.check_call(
                ['git', 'branch', '-d', repo+'_bump'])


    def checkout_branch(self, existing_pr, repo):
        '''
        Checkout branch for the bump
        '''
        if not existing_pr:
            if self.fork_exists:  # fork exists for other repo and old branch for this repo
                self.delete_old_branch()
                subprocess.check_call(
                    ['git', 'pull', 'https://github.com/jupyterhub/mybinder.org-deploy.git', 'master'])
            subprocess.check_call(
                ['git', 'checkout', '-b', repo+'_bump'])
        else:
            subprocess.check_call(
                ['git', 'checkout', repo+'_bump'])      


    def edit_repo2docker_files(self, upgrade, existing_pr):
        '''
        Update the SHA to latest for r2d
        '''
        with open('mybinder/values.yaml', 'r', encoding='utf8') as f:
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
        with open(fname, 'w', encoding='utf8') as f:
            f.write(updated_yaml)

        return [fname]


    def edit_binderhub_files(self, upgrade, existing_pr):
        '''
        Update the SHA to latest for bhub
        '''
        with open('mybinder/requirements.yaml', 'r', encoding='utf8') as f:
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
        with open(fname, 'w', encoding='utf8') as f:
            f.write(updated_yaml)

        return [fname]


    def edit_files(self, upgrade, existing_pr):
        '''
        Controlling method to update file for the repo
        '''
        if upgrade == 'repo2docker':
            return self.edit_repo2docker_files(upgrade, existing_pr)

        elif upgrade == 'binderhub':
            return self.edit_binderhub_files(upgrade, existing_pr)


    def add_commit_push(self, files_changed, repo):
        '''
        After making change, add, commit and push to fork
        '''
        for f in files_changed:
            subprocess.check_call(['git', 'add', f])

        if repo == 'repo2docker':
            commit_message = 'repo2docker: https://github.com/jupyter/repo2docker/compare/{}...{}'.format(
                self.commit_info['repo2docker']['live'].split('.dirty')[0].split('.')[-1][1:], self.commit_info['repo2docker']['latest'].split('.dirty')[0].split('.')[-1][1:])
        elif repo == 'binderhub':
            commit_message = 'binderhub: https://github.com/jupyterhub/binderhub/compare/{}...{}'.format(
                self.commit_info['binderhub']['live'], self.commit_info['binderhub']['latest'])

        subprocess.check_call(['git', 'config', 'user.name', 'henchbot'])
        subprocess.check_call(['git', 'config', 'user.email', 'henchbot.github@gmail.com'])
        subprocess.check_call(['git', 'commit', '-m', commit_message])
        subprocess.check_call(['git', 'push', 'https://henchbot:{}@github.com/henchbot/mybinder.org-deploy'.format(TOKEN), repo+'_bump'])


    def upgrade_repo_commit(self, existing_pr, repo):
        '''
        Main controlling method for the update
        '''
        if not self.fork_exists:
            self.make_fork()
        self.clone_fork()

        os.chdir('mybinder.org-deploy')
        self.checkout_branch(existing_pr, repo)
        files_changed = self.edit_files(repo, existing_pr)
        self.add_commit_push(files_changed, repo)
        os.chdir('..')
        shutil.rmtree('mybinder.org-deploy')

        self.create_update_pr(repo, existing_pr)


    def get_associated_prs(self, compare_url):
        '''
        Gets all PRs from dependency repo associated with the upgrade
        '''
        repo_api = compare_url.replace('github.com', 'api.github.com/repos')
        pr_api = repo_api.split('/compare/')[0] + '/pulls/'
        res = requests.get(repo_api).json()
        commit_shas = [x['sha'] for x in res['commits']]

        associated_prs = ['Associated PRs:']
        for sha in commit_shas[::-1]:
            res = requests.get('https://api.github.com/search/issues?q=sha:{}'.format(sha)).json()
            if 'items' in res:
                for i in res['items']:
                    formatted = '- {} [#{}]({})'.format(i['title'], i['number'], i['html_url'])
                    repo_owner = i['repository_url'].split('/')[-2]
                    try:
                        merged_at = requests.get(pr_api + str(i['number'])).json()['merged_at']
                    except KeyError:
                        continue
                    if formatted not in associated_prs and repo_owner.startswith('jupyter') and merged_at:
                        associated_prs.append(formatted)
            time.sleep(3)

        return associated_prs


    def make_pr_body(self, repo):
        '''
        Formats a text body for the PR
        '''
        if repo == 'repo2docker':
            compare_url = 'https://github.com/jupyter/repo2docker/compare/{}...{}'.format(
                                self.commit_info['repo2docker']['live'].split('.dirty')[0].split('.')[-1][1:], 
                                self.commit_info['repo2docker']['latest'].split('.dirty')[0].split('.')[-1][1:])
            associated_prs = self.get_associated_prs(compare_url)
            body = '\n'.join(['This is a repo2docker version bump. See the link below for a diff of new changes:\n', compare_url + ' \n'] + associated_prs)

        elif repo == 'binderhub':
            compare_url = 'https://github.com/jupyterhub/binderhub/compare/{}...{}'.format(
                                self.commit_info['binderhub']['live'], 
                                self.commit_info['binderhub']['latest'])
            associated_prs = self.get_associated_prs(compare_url)
            body = '\n'.join(['This is a binderhub version bump. See the link below for a diff of new changes:\n', compare_url + ' \n'] + associated_prs)

        return body


    def create_update_pr(self, repo, existing_pr):
        '''
        Makes the PR from all components
        '''
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
        '''
        Get the live BinderHub SHA from mybinder.org
        '''
        # Load master requirements
        url_requirements = "https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/requirements.yaml"
        requirements = load(requests.get(url_requirements).text)
        binderhub_dep = [ii for ii in requirements[
            'dependencies'] if ii['name'] == 'binderhub'][0]
        bhub_live = binderhub_dep['version'].split('-')[-1]
        self.commit_info['binderhub']['live'] = bhub_live


    def get_jupyterhub_live(self):
        '''
        Get the live JupyterHub SHA from mybinder.org
        '''
        url_binderhub_requirements = "https://raw.githubusercontent.com/jupyterhub/binderhub/{}/helm-chart/binderhub/requirements.yaml".format(
            self.commit_info['binderhub']['live'])
        requirements = load(requests.get(url_binderhub_requirements).text)
        jupyterhub_dep = [ii for ii in requirements[
            'dependencies'] if ii['name'] == 'jupyterhub'][0]
        jhub_live = jupyterhub_dep['version'].split('-')[-1]
        self.commit_info['jupyterhub']['live'] = jhub_live


    def get_repo2docker_live(self):
        '''
        Get the live r2d SHA from mybinder.org
        '''
        # Load master repo2docker
        url_helm_chart = "https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/values.yaml"
        helm_chart = requests.get(url_helm_chart)
        helm_chart = load(helm_chart.text)
        r2d_live = helm_chart['binderhub']['config'][
            'BinderHub']['build_image'].split(':')[-1]
        self.commit_info['repo2docker']['live'] = r2d_live


    def get_repo2docker_latest(self):
        '''
        Get the latest r2d SHA from DockerHub
        '''
        # Load latest r2d commit from dockerhub
        url = "https://hub.docker.com/v2/repositories/jupyter/repo2docker/tags/"
        resp = requests.get(url)
        r2d_master = resp.json()['results'][0]['name']
        self.commit_info['repo2docker']['latest'] = r2d_master
        print('repo2docker', self.commit_info['repo2docker']['live'], self.commit_info['repo2docker']['latest'])


    def get_bhub_jhub_latest(self):
        '''
        Get the latest bhub SHA from the helm chart
        '''
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
        '''
        Main controlling method to get commit SHAs
        '''
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
