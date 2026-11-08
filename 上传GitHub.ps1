# 把「番茄文档」推到你的 GitHub 仓库
#
# 用法一（推荐）：先在此电脑的浏览器里登录 GitHub，然后直接跑：
#     powershell -ExecutionPolicy Bypass -File .\上传GitHub.ps1
# 它会用 Git 自带的凭据管理器弹出登录窗口，登录后自动建仓库并 push。
#
# 用法二：自己有 Personal Access Token（勾 repo 权限）：
#     $env:GITHUB_TOKEN = "ghp_xxx"
#     powershell -ExecutionPolicy Bypass -File .\上传GitHub.ps1
#
# 可选：指定仓库名 / 是否私有
#     $env:REPO_NAME = "tomato-word"
#     $env:REPO_PRIVATE = "false"

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$repoName = if ($env:REPO_NAME) { $env:REPO_NAME } else { "tomato-word" }
$private = if ($env:REPO_PRIVATE -eq "true") { $true } else { $false }
$desc = "把番茄小说伪装成 Word 2019 窗口的 PC 阅读器（Tkinter + 官网字体混淆解码）"

function Get-Token {
    if ($env:GITHUB_TOKEN) { return $env:GITHUB_TOKEN.Trim() }
    if ($env:GH_TOKEN) { return $env:GH_TOKEN.Trim() }
    $sec = Read-Host "请粘贴 GitHub Personal Access Token（输入不显示）" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Invoke-GH {
    param($Method, $Path, $Body)
    $headers = @{
        "Authorization" = "Bearer $script:token"
        "Accept"        = "application/vnd.github+json"
        "User-Agent"    = "TomatoWord-Uploader"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $uri = "https://api.github.com$Path"
    if ($Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -Body ($Body | ConvertTo-Json -Depth 5) -ContentType "application/json; charset=utf-8" -TimeoutSec 40
    } else {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -TimeoutSec 40
    }
}

Write-Host "=== 1/5 校验 token ===" -ForegroundColor Cyan
$script:token = Get-Token
if (-not $script:token) { throw "没有拿到 token。" }

$me = Invoke-GH -Method GET -Path "/user"
$owner = $me.login
$login_id = $me.id
Write-Host ("已登录为 {0}（id={1}）" -f $owner, $login_id) -ForegroundColor Green

Write-Host "=== 2/5 检查 / 创建仓库 ===" -ForegroundColor Cyan
$fullName = "$owner/$repoName"
$exists = $false
try {
    $null = Invoke-GH -Method GET -Path "/repos/$fullName"
    $exists = $true
    Write-Host "仓库已存在：$fullName" -ForegroundColor Yellow
} catch {
    Write-Host "仓库还不存在，创建中…"
}

if (-not $exists) {
    $created = Invoke-GH -Method POST -Path "/user/repos" -Body @{
        name        = $repoName
        description = $desc
        private     = $private
        has_issues  = $true
        has_wiki    = $false
        auto_init   = $false
    }
    Write-Host ("已创建：{0}" -f $created.html_url) -ForegroundColor Green
    $htmlUrl = $created.html_url
} else {
    $htmlUrl = "https://github.com/$fullName"
}

Write-Host "=== 3/5 设置 remote ===" -ForegroundColor Cyan
$pushUrl = "https://x-access-token:$($script:token)@github.com/$fullName.git"
git remote remove origin 2>$null
git remote add origin $pushUrl
git branch -M main
Write-Host "remote 就绪（地址里临时带 token，push 完会清掉）"

Write-Host "=== 4/5 推送 ===" -ForegroundColor Cyan
git push -u origin main --force
if ($LASTEXITCODE -ne 0) {
    git remote set-url origin "https://github.com/$fullName.git"
    throw "push 失败（退出码 $LASTEXITCODE）。请检查 token 是否有 repo 权限、仓库是否为空。"
}

Write-Host "=== 5/5 清理 remote 里的 token ===" -ForegroundColor Cyan
git remote set-url origin "https://github.com/$fullName.git"
git remote -v

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " 上传成功！" -ForegroundColor Green
Write-Host " 仓库地址： $htmlUrl" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "接下来建议手动做两件事（需要点网页）："
Write-Host "  1. 打 Release 标签，把 番茄文档-v1.8.zip 作为附件上传（仓库里没放 exe）"
Write-Host "  2. 在仓库 Settings 里补 Description / Topics"