# Spotify Tracker

This repository contains the Spotify Tracker project.

## Version control

The repository starts on the `master` branch. Create a branch for each change:

```sh
git switch -c codex/describe-your-change
```

Review and commit your work:

```sh
git status
git diff
git add <files>
git diff --cached
git commit -m "Describe the change"
```

Local environment files, credentials, dependencies, build output, and local
databases are ignored. Keep Spotify client secrets and access tokens in an
ignored `.env` file. An `.env.example` file may be committed with placeholder
values to document required configuration.

## Remote repository

No remote repository is configured yet. After creating an empty remote repository,
connect it and push the initial history:

```sh
git remote add origin <repository-url>
git push -u origin master
```
