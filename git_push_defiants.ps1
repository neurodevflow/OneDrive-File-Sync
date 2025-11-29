param([string]$Remote)
if (-not $Remote) { throw 'Remote required' }
git init
git add .
git commit -m 'Initial commit'
git branch -M main
git remote add origin $Remote
git push -u origin main
