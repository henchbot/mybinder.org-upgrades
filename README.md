# Automating mybinder.org dependency upgrades in 10 steps

[BinderHub](https://github.com/jupyterhub/binderhub) and [repo2docker](https://github.com/jupyterhub/repo2docker) are key components of the service [mybinder.org](https://mybinder.org); depending on them for the latest functionality demands that mybinder.org be continuously upgraded with respect to these repositories to ensure users are getting the best experience. Moreover, to avoid merging in massive updates at irregular intervals, it is desirable to merge updates in frequent intervals of smaller changes in order to more easily identify any breaking changes from the dependency upgrades.

This bot is deployed at https://dashboard.heroku.com/apps/ancient-chamber-80926. Merging PRs into `master` in this repository will cause an automatic deploy to Heroku.

While this process done manually only takes a few minutes following processes outlined in the "[Site Reliability Guide](https://mybinder-sre.readthedocs.io/en/latest/)", it's prone to human error messing with all the SHAs and the team must remember to regularly do it in the first place. In the interest of automation, a bot was built to relieve this burden, and we've decided to highlight its functionality in this blogpost!

At a high level, this is what we want our bot to do:

- Diff the current commit hash for both repo2docker and BinderHub repos with the deployed versions in the mybinder.org repo. If either or both are different:
	- Fork the respective dependency repo
	- Clone the fork locally
	- Checkout a new branch for the bump
	- Make the appropriate edit to update the commit hash in the mybinder.org repo
	- Add and commit the change
	- Push to the branch in the forked repo
	- Create a PR to the main mybinder.org repo
	- Remove the locally cloned repo

Additionally, it would be ideal if the bot could update an existing PR instead of creating new ones for the version bumps. We'd also like to provide some information in the comments of the PR as to what high level changes were made so we have some idea about what we're merging in.

Here's what we're aiming for:

The PR body:
![](https://i.imgur.com/DEcogdu.png)

The PR diff:
![](https://i.imgur.com/K9Le5ig.png)

Now that we've broken it down a bit, let's write up some Python code. Once we have a functioning script, we can worry about how we will run this in the cloud (cron job vs. web app).

# Writing the bot

If you don't care about the step-by-step, you can skip to the [final version of the code](https://github.com/henchbot/mybinder.org-upgrades/blob/master/henchbot.py).

In the interest of linear understanding and simplicity for a tutorial, the step-by-step below will not write functions or classes but just list the raw code necessary to carry out the tasks. The final version of the code linked above is one way to refactor it.

## Step 1: Retrieve current deployed mybinder.org dependency versions

The first step is to see if any changes are necessary in the first place. Fortunately, [@choldgraf](https://github.com/choldgraf) had already made a [script](https://github.com/jupyterhub/mybinder.org-deploy/blob/master/scripts/list_new_commits.py) to do this.

To find the current live commit SHA for BinderHub in mybinder.org, we simply check the [`requirements.yaml`](https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/requirements.yaml) file. We'll need Python's `yaml` and `requests` modules to make the GET request and parse the yaml in the response. Note that this is also conveniently the file we'd want to change to upgrade the version.

```python
from yaml import safe_load as load
import requests

url_requirements = "https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/requirements.yaml"
requirements = load(requests.get(url_requirements).text)
binderhub_dep = [ii for ii in requirements['dependencies'] if ii['name'] == 'binderhub'][0]
bhub_live = binderhub_dep['version'].split('-')[-1]
print(bhub_live)
```

Similarly, for repo2docker, we check the mybinder.org [`values.yaml`](https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/values.yaml) file:

```python
url_helm_chart = "https://raw.githubusercontent.com/jupyterhub/mybinder.org-deploy/master/mybinder/values.yaml"
helm_chart = requests.get(url_helm_chart)
helm_chart = load(helm_chart.text)
r2d_live = helm_chart['binderhub']['config']['BinderHub']['build_image'].split(':')[-1]
print(r2d_live)
```

Let's store these SHAs in a dictionary we can use for later reference:

```python
commit_info = {'repo2docker': {}
               'binderhub': {}}

commit_info['binderhub']['live'] = bhub_live
commit_info['repo2docker']['live'] = r2d_live
print(commit_info)
```

## Step 2: Retrieve lastest commits from the dependency repos

When we get the latest commit SHAs for repo2docker and BinderHub, we need to be careful and make sure we don't automatically grab the latest one from GitHub. The travis build for mybinder.org looks for the repo2docker Docker image from [DockerHub](https://hub.docker.com/v2/repositories/jupyter/repo2docker/tags/), and the latest BinderHub from the [JupyterHub helm chart](https://raw.githubusercontent.com/jupyterhub/helm-chart/gh-pages/index.yaml).

Let's get the repo2docker version first:

```python
url = "https://hub.docker.com/v2/repositories/jupyter/repo2docker/tags/"
resp = requests.get(url)
r2d_master = resp.json()['results'][0]['name']
print(r2d_master)
```

Now we can do BinderHub:

```python
url_helm_chart = 'https://raw.githubusercontent.com/jupyterhub/helm-chart/gh-pages/index.yaml'
helm_chart_yaml = load(requests.get(url_helm_chart).text)

# sort by date created
updates_sorted = sorted(helm_chart_yaml['entries']['binderhub'], key=lambda k: k['created'])
bh_master = updates_sorted[-1]['version'].split('-')[-1]
print(bh_master)
```

Let's add these to our dictionary too:

```python
# add to commit_info dictionary
commit_info['repo2docker']['latest'] = r2d_master
print('repo2docker', commit_info['repo2docker']['live'], commit_info['repo2docker']['latest'])

commit_info['binderhub']['latest'] = bh_master
print('binderhub', commit_info['binderhub']['live'], commit_info['binderhub']['latest'])
print(commit_info)
```

Great, now we should have all the information we need to determine *whether* an update needs to be made or not, *and* what the new commit SHA should be!

## Step 3: Fork mybinder.org repo

If we determine an upgrade for the repo is necessary, we need to fork the mybinder.org [repository](https://github.com/jupyterhub/mybinder.org-deploy), make the change, commit, push, and make a PR. Fortunately, the GitHub API has all the functionality we need! Let's just make a fork first.

If you have permissions to a bunch of repos and organizations on GitHub, you may want to [create a new account or organization](https://help.github.com/en/articles/signing-up-for-a-new-github-account) so that you don't accidentally start automating git commands through an account that has write access to so much, especially while developing and testing the bot. I created the [henchbot](https://github.com/henchbot) account for this.

Once you know which account you want to be making the PRs with, you'll need to [create a personal access token](https://help.github.com/en/articles/creating-a-personal-access-token-for-the-command-line) from within that account. I've set this as an environment variable so it isn't hard-coded in the script.

```python
import os

TOKEN = os.environ.get('HENCHBOT_TOKEN')

for repo in ['binderhub', 'repo2docker']:
	if commit_info[repo]['live'] != commit_info[repo]['latest']:
		res = requests.post('https://api.github.com/repos/jupyterhub/mybinder.org-deploy/forks',
		            headers={'Authorization': 'token {}'.format(TOKEN)})
```

Using the API for a post request to the `forks` endpoint will fork the repo to your account. That's it!

## Step 4: Clone your fork

You should be quite used to this! We'll use Python's `subprocess` module to run all of our `bash` commands. We'll need to run these within the for-loop above.

```python
		subprocess.check_call(['git', 'clone', 'https://github.com/henchbot/mybinder.org-deploy'])
```

Let's also `cd` into it and check out a new branch.

```python
		os.chdir('mybinder.org-deploy')
		subprocess.check_call(['git', 'checkout', '-b', '{}_bump'.format(repo)])
```

## Step 5: Make the file changes

Now we need to acutally edit the file like we would for an upgrade.

For repo2docker, we edit the same `values.yaml` file we checked above and replace the old SHA ("live") with the "lastest".

```python
		if repo == 'repo2docker':
			with open('mybinder/values.yaml', 'r') as f:
			    values_yaml = f.read()

			updated_yaml = values_yaml.replace(
			    "jupyter/repo2docker:{}".format(
			        commit_info[upgrade]['live']),
			    "jupyter/repo2docker:{}".format(
			        commit_info[upgrade]['latest']))   

			fname = 'mybinder/values.yaml'
			with open(fname, 'w') as f:
			    f.write(updated_yaml)
```

For BinderHub, we edit the same `requirements.yaml` file we checked above and replace the old SHA ("live") with the "latest".

```python
		elif repo == 'binderhub':
			with open('mybinder/requirements.yaml', 'r') as f:
			    requirements_yaml = f.read()

			updated_yaml = requirements_yaml.replace(
			    "version: 0.2.0-{}".format(commit_info[upgrade]['live']),
			    "version: 0.2.0-{}".format(commit_info[upgrade]['latest']))   

			fname = 'mybinder/requirements.yaml'
			with open(fname, 'w') as f:
			    f.write(updated_yaml)
```

## Step 6: Stage, commit, push

Now that we've edited the correct files, we can stage and commit the changes. We'll make the commit message the name of the repo and the compare URL for the commit changes so people can see what has changed between versions for the dependency.

```python
		# use var fname from editing step
		subprocess.check_call(['git', 'add', fname])

		if repo == 'repo2docker':
		    commit_message = 'repo2docker: https://github.com/jupyterhub/repo2docker/compare/{}...{}'.format(
		        commit_info['repo2docker']['live'],commit_info['repo2docker']['latest'])
		elif repo == 'binderhub':
		    commit_message = 'binderhub: https://github.com/jupyterhub/binderhub/compare/{}...{}'.format(
		        commit_info['binderhub']['live'], commit_info['binderhub']['latest'])

		subprocess.check_call(['git', 'config', 'user.name', 'henchbot'])
		subprocess.check_call(['git', 'config', 'user.email', 'henchbot.github@gmail.com'])
		subprocess.check_call(['git', 'commit', '-m', commit_message])
		subprocess.check_call(['git', 'push', 'https://henchbot:{}@github.com/henchbot/mybinder.org-deploy'.format(TOKEN), repo+'_bump'])
```

Awesome, we now have a fully updated fork ready to make a PR to the main repo!

## Step 7: Make the body for the PR

We want the PR to have a nice comment explaining what's happening and linking any helpful information so that the merger knows what they're doing. We'll note that this is a version bump and link the URL diff so it can be clicked to see what has changed.

```python
		if repo == 'repo2docker':
		    compare_url = 'https://github.com/jupyterhub/repo2docker/compare/{}...{}'.format(
		                        commit_info['repo2docker']['live'], 
		                        commit_info['repo2docker']['latest'])
		    body = '\n'.join(['This is a repo2docker version bump. See the link below for a diff of new changes:\n', compare_url + ' \n'])

		elif repo == 'binderhub':
		    compare_url = 'https://github.com/jupyterhub/binderhub/compare/{}...{}'.format(
		                        commit_info['binderhub']['live'], 
		                        commit_info['binderhub']['latest'])
		    body = '\n'.join(['This is a binderhub version bump. See the link below for a diff of new changes:\n', compare_url + ' \n'])
```

## Step 8: Make the PR

We can use the GitHub API to make a pull request by calling the `pulls` endpoint with the `title`, `body`, `base`, and `head`. We'll use the nice body we formatted above, call the title the same as the commit message we made with the repo name and the two SHAs, and put the `base` as `master` and the `head` the name of our fork. Then we just make a POST request to the `pulls` endpoint of the main repo.

```python
		pr = {
		    'title': '{}: {}...{}'.format(repo,
		                                  commit_info[repo]['live'],
		                                  commit_info[repo]['latest']),
		    'body': body,
		    'base': 'master',
		    'head': 'henchbot:{}_bump'.format(repo)}

		res = requests.post('https://api.github.com/repos/jupyterhub/mybinder.org-deploy/pulls',
		    headers={'Authorization': 'token {}'.format(TOKEN)}, json=pr)
```

## Step 9: Confirm and merge!

If we check the [mybinder.org PRs](https://github.com/jupyterhub/mybinder.org-deploy/pulls), we would now see the automated PR from our account!

## Step 10: Automating the script (cron)

Now that we have a script we can simply execute to create a PR (`$ python henchbot.py`), we want to make this as hands-off as possible. Generally we have two options: (1) set this script to be run as a [cron job](https://en.wikipedia.org/wiki/Cron); (2) have a web app listener that gets pinged whenever a change is made and executes your script as a reaction to the ping.

Given that these aren't super urgent updates that need to be made seconds or minutes after a repository update, we will go for the easier and less comptutationally-expensive option of cron.

If you aren't familiar with cron, it's simply a system program that will run whatever command you want at whatever time or time interval you want. For now, we've decided that we want to execute this script every hour. 

Cron can be run on your local computer (though it would need to be continuously running) or a remote server. I've elected to throw it on my raspberry pi, which is always running. Since I have a few projects going on, I like to keep the cron jobs in a file.

```bash
$ vim crontab-jobs
```

You can define your cron jobs here with the correct syntax (space-separated). Check out [this site](https://crontab.guru/every-1-hour) for help with the crontab syntax. Since we want to run this every hour, we will set it to run on the 0 minutes, for every hour, every day, every month, every year. We also need to make sure it has the correct environment variable with the GitHub personal access token we created, so we'll add that to the command.

```
0 * * * * cd /home/pi/projects/mybinder.org-upgrades && HENCHBOT_TOKEN='XXXXX' /home/pi/miniconda3/bin/python henchbot.py
```

Now we point our cron to the file we've created to load the jobs.

```bash
$ crontab crontab-jobs
```

To see our active crontab, we can list it:

```bash
$ crontab -l
```

That's it! At the top of every hour, our bot will check to see if an update needs to be made, and if so, create a PR. To clean up files and handle existing PRs, in addition to some other details, I've written a few other functions. It is also implemented as a class with appropriate methods. You can check out the final code [here](https://github.com/henchbot/mybinder.org-upgrades/blob/master/henchbot.py).
