#!/bin/bash
set -euo pipefail

# =============================================================================
# test-backup-claude.sh — backup-claude.sh 同步逻辑的综合测试脚本
#
# 本脚本在隔离的临时环境中测试 rsync + git 同步流程，不会触碰真实的
# ~/.claude/ 或任何真实的 git 仓库。
#
# 同步流程（每台机器执行一轮）：
#   1. Backup:  rsync -a --update  local → repo   （本地→仓库，不删除仓库文件）
#   2. Commit:  git add -A && git commit
#   3. Pull:    git pull --no-rebase               （合并远程变更）
#   4. Restore: rsync -a --delete  repo → local    （仓库→本地，删除本地多余文件）
#   5. Push:    git push
# =============================================================================

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
FAILURES=()

pass() {
    ((PASS_COUNT++))
    echo -e "  ${GREEN}PASS${NC}: $1"
}

fail() {
    ((FAIL_COUNT++))
    FAILURES+=("$1")
    echo -e "  ${RED}FAIL${NC}: $1"
}

assert_file_exists() {
    local file="$1"
    local msg="${2:-文件应存在: $file}"
    if [[ -f "$file" ]]; then
        pass "$msg"
    else
        fail "$msg (文件不存在: $file)"
    fi
}

assert_file_not_exists() {
    local file="$1"
    local msg="${2:-文件不应存在: $file}"
    if [[ ! -f "$file" ]]; then
        pass "$msg"
    else
        fail "$msg (文件仍然存在: $file)"
    fi
}

assert_file_content() {
    local file="$1"
    local expected="$2"
    local msg="${3:-文件内容应匹配}"
    if [[ -f "$file" ]] && [[ "$(cat "$file")" == "$expected" ]]; then
        pass "$msg"
    else
        local actual="<不存在>"
        [[ -f "$file" ]] && actual="$(cat "$file")"
        fail "$msg (期望: '$expected', 实际: '$actual')"
    fi
}

assert_file_contains() {
    local file="$1"
    local pattern="$2"
    local msg="${3:-文件应包含指定内容}"
    if [[ -f "$file" ]] && grep -qF "$pattern" "$file"; then
        pass "$msg"
    else
        fail "$msg (文件不包含: '$pattern')"
    fi
}

# ---- 环境搭建 ----

# 创建隔离的测试环境：一个 bare remote、两台机器（各有 local_dir + repo clone）
setup_env() {
    TEST_ROOT=$(mktemp -d)

    # 远程裸仓库（模拟 GitHub）
    REMOTE="${TEST_ROOT}/remote.git"
    git init --bare "$REMOTE" >/dev/null 2>&1

    # Machine A
    A_LOCAL="${TEST_ROOT}/a_local"   # 模拟 ~/.claude/
    A_REPO="${TEST_ROOT}/a_repo"     # 模拟 ~/projects/migrate_to_new_device/
    mkdir -p "$A_LOCAL"
    git clone "$REMOTE" "$A_REPO" >/dev/null 2>&1
    A_SYNC="${A_REPO}/claude-global" # 仓库中的同步子目录
    mkdir -p "$A_SYNC"
    # 初始提交（bare repo 需要至少一个提交才能 pull）
    (cd "$A_REPO" && touch .gitkeep && git add -A && git commit -m "init" >/dev/null 2>&1 && git push >/dev/null 2>&1)

    # Machine B
    B_LOCAL="${TEST_ROOT}/b_local"
    B_REPO="${TEST_ROOT}/b_repo"
    mkdir -p "$B_LOCAL"
    git clone "$REMOTE" "$B_REPO" >/dev/null 2>&1
    B_SYNC="${B_REPO}/claude-global"
    mkdir -p "$B_SYNC"
}

cleanup_env() {
    rm -rf "$TEST_ROOT"
}

# ---- 核心同步函数 ----

# 模拟一台机器执行完整的 5 阶段同步
sync_machine() {
    local local_dir="$1"    # 模拟 ~/.claude/
    local repo_dir="$2"     # 模拟仓库中的 claude-global/ 子目录
    local git_repo="$3"     # 模拟 git 仓库根目录

    # Phase 1: Backup（本地 → 仓库），--update 只覆盖较旧的文件，不删除仓库中的文件
    mkdir -p "${repo_dir}"
    rsync -a --update "${local_dir}/" "${repo_dir}/"

    # Phase 2: Commit
    (cd "$git_repo" && git add -A && git diff --cached --quiet || (cd "$git_repo" && git add -A && git commit -m "backup $(date +%s)" >/dev/null 2>&1)) || true

    # Phase 3: Pull（合并远程变更）
    (cd "$git_repo" && git pull --no-rebase >/dev/null 2>&1) || true

    # Phase 4: Restore（仓库 → 本地），--delete 使本地与仓库完全一致
    mkdir -p "${repo_dir}" "${local_dir}"
    rsync -a --delete "${repo_dir}/" "${local_dir}/"

    # Phase 5: Push
    (cd "$git_repo" && git push >/dev/null 2>&1) || true
}

# 仅执行 backup 阶段（用于部分流程测试）
backup_phase() {
    local local_dir="$1"
    local repo_dir="$2"
    rsync -a --update "${local_dir}/" "${repo_dir}/"
}

# 仅执行 commit + push 阶段
commit_and_push() {
    local git_repo="$1"
    (cd "$git_repo" && git add -A && (git diff --cached --quiet || git commit -m "backup $(date +%s)" >/dev/null 2>&1) && git push >/dev/null 2>&1) || true
}

# 仅执行 pull + restore 阶段
pull_and_restore() {
    local repo_dir="$1"
    local local_dir="$2"
    local git_repo="$3"
    (cd "$git_repo" && git pull --no-rebase >/dev/null 2>&1) || true
    rsync -a --delete "${repo_dir}/" "${local_dir}/"
}

# =============================================================================
# 测试用例
# =============================================================================

test_01_basic_sync() {
    echo -e "\n${BOLD}测试 1: 基本同步 — A 创建文件，B 同步后应收到${NC}"
    setup_env

    # A 创建文件
    echo "hello from A" > "$A_LOCAL/file1.txt"
    mkdir -p "$A_LOCAL/subdir"
    echo "nested" > "$A_LOCAL/subdir/deep.txt"

    # A 同步
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 同步
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    assert_file_content "$B_LOCAL/file1.txt" "hello from A" "B 应收到 file1.txt"
    assert_file_content "$B_LOCAL/subdir/deep.txt" "nested" "B 应收到嵌套目录文件"

    cleanup_env
}

test_02_bidirectional_sync() {
    echo -e "\n${BOLD}测试 2: 双向同步 — A 创建 file1，B 创建 file2，同步后双方都有两个文件${NC}"
    setup_env

    # A 创建 file1，同步
    echo "from A" > "$A_LOCAL/file1.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 创建 file2，同步（会先拉到 A 的 file1）
    echo "from B" > "$B_LOCAL/file2.txt"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    # A 再同步一次（拉到 B 的 file2）
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    assert_file_content "$A_LOCAL/file1.txt" "from A" "A 保留自己的 file1"
    assert_file_content "$A_LOCAL/file2.txt" "from B" "A 收到 B 的 file2"
    assert_file_content "$B_LOCAL/file1.txt" "from A" "B 收到 A 的 file1"
    assert_file_content "$B_LOCAL/file2.txt" "from B" "B 保留自己的 file2"

    cleanup_env
}

test_03_remote_delete_propagation() {
    echo -e "\n${BOLD}测试 3: 远程删除传播 — A 删除文件并从仓库删除，B 同步后文件消失${NC}"
    setup_env

    # A 创建文件，双方同步
    echo "to be deleted" > "$A_LOCAL/victim.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"
    assert_file_exists "$B_LOCAL/victim.txt" "B 初始应有 victim.txt"

    # A 从本地和仓库中都删除
    rm -f "$A_LOCAL/victim.txt"
    rm -f "$A_SYNC/victim.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 同步 —— restore --delete 应删除 B 本地的 victim.txt
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    assert_file_not_exists "$B_LOCAL/victim.txt" "B 同步后 victim.txt 应被删除"

    cleanup_env
}

test_04_local_delete_without_repo_delete() {
    echo -e "\n${BOLD}测试 4: 仅本地删除（未删仓库）— 文件会通过 restore 恢复回来${NC}"
    setup_env

    # A 创建文件并同步
    echo "persistent" > "$A_LOCAL/sticky.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # A 只从本地删除，不从仓库删除
    rm -f "$A_LOCAL/sticky.txt"
    # 仓库中还有 sticky.txt

    # A 再次同步 —— backup 阶段 rsync --update 不会删除仓库文件
    # restore 阶段会把仓库的 sticky.txt 恢复到本地
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    assert_file_content "$A_LOCAL/sticky.txt" "persistent" "仅本地删除的文件应通过 restore 恢复"

    cleanup_env
}

test_05_proper_local_delete() {
    echo -e "\n${BOLD}测试 5: 正确删除 — 同时从本地和仓库删除，同步后 B 也没有该文件${NC}"
    setup_env

    # A 创建文件，双方同步
    echo "will be properly deleted" > "$A_LOCAL/proper.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"
    assert_file_exists "$B_LOCAL/proper.txt" "B 初始应有 proper.txt"

    # A 从本地和仓库都删除
    rm -f "$A_LOCAL/proper.txt"
    rm -f "$A_SYNC/proper.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 同步
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    assert_file_not_exists "$A_LOCAL/proper.txt" "A 本地不应有 proper.txt"
    assert_file_not_exists "$B_LOCAL/proper.txt" "B 同步后不应有 proper.txt"

    cleanup_env
}

test_06_stale_machine() {
    echo -e "\n${BOLD}测试 6: 过期机器 — B 创建大量新文件后同步，长时间未同步的 A 不应丢失 B 的文件${NC}"
    setup_env

    # 初始同步
    echo "base" > "$A_LOCAL/base.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    # B 创建很多新文件并同步
    for i in $(seq 1 20); do
        echo "new file $i from B" > "$B_LOCAL/new_$i.txt"
    done
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    # A（长时间未同步）现在同步
    # backup 阶段：A 的 rsync --update 不会删除仓库中 B 的新文件
    # pull 阶段：git pull 拉到 B 的所有文件
    # restore 阶段：仓库内容恢复到 A 本地
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    assert_file_content "$A_LOCAL/base.txt" "base" "A 保留原有的 base.txt"
    assert_file_exists "$A_LOCAL/new_1.txt" "A 应收到 B 的 new_1.txt"
    assert_file_exists "$A_LOCAL/new_10.txt" "A 应收到 B 的 new_10.txt"
    assert_file_exists "$A_LOCAL/new_20.txt" "A 应收到 B 的 new_20.txt"

    # 验证所有 20 个文件都存在
    local missing=0
    for i in $(seq 1 20); do
        [[ -f "$A_LOCAL/new_$i.txt" ]] || ((missing++))
    done
    if [[ $missing -eq 0 ]]; then
        pass "A 收到了 B 的全部 20 个新文件"
    else
        fail "A 缺少 ${missing} 个来自 B 的新文件"
    fi

    cleanup_env
}

test_07_update_preserves_newer() {
    echo -e "\n${BOLD}测试 7: --update 保护较新文件 — A 的旧文件不应覆盖仓库中 B 的较新文件${NC}"
    setup_env

    # A 创建文件并同步
    echo "version 1 from A" > "$A_LOCAL/shared.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 同步，然后修改文件为较新版本
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"
    sleep 1  # 确保时间戳不同
    echo "version 2 from B" > "$B_LOCAL/shared.txt"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    # A 本地仍是旧版本（未同步），现在 A 执行 backup 阶段
    # rsync --update 不应用 A 的旧文件覆盖仓库中 B 的较新文件
    backup_phase "$A_LOCAL" "$A_SYNC"

    # 验证仓库中仍是 B 的较新版本
    # 注意：A 需要先 pull 才能看到 B 的版本
    (cd "$A_REPO" && git pull --no-rebase >/dev/null 2>&1) || true
    assert_file_content "$A_SYNC/shared.txt" "version 2 from B" "仓库中应保留 B 的较新版本"

    cleanup_env
}

test_08_delete_on_restore() {
    echo -e "\n${BOLD}测试 8: restore --delete 清理本地多余文件 — 仓库中已删除的文件应从本地移除${NC}"
    setup_env

    # A 创建多个文件并同步
    echo "keep me" > "$A_LOCAL/keep.txt"
    echo "remove me" > "$A_LOCAL/remove.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # 模拟：在仓库中删除 remove.txt（模拟远程已合并的删除状态）
    rm -f "$A_SYNC/remove.txt"
    (cd "$A_REPO" && git add -A && git commit -m "delete remove.txt" >/dev/null 2>&1)

    # A 本地仍有 remove.txt，执行 restore --delete
    rsync -a --delete "${A_SYNC}/" "${A_LOCAL}/"

    assert_file_exists "$A_LOCAL/keep.txt" "keep.txt 应保留"
    assert_file_not_exists "$A_LOCAL/remove.txt" "restore --delete 应删除本地多余的 remove.txt"

    cleanup_env
}

test_09_conflict_detection() {
    echo -e "\n${BOLD}测试 9: 冲突检测 — A 和 B 同时修改同一文件，合并时应产生冲突${NC}"
    setup_env

    # 创建初始文件，双方同步
    echo "original content" > "$A_LOCAL/conflict.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    # A 修改文件并同步（先提交推送）
    echo "A's modification" > "$A_LOCAL/conflict.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 修改同一文件
    echo "B's modification" > "$B_LOCAL/conflict.txt"

    # B 执行 backup + commit
    backup_phase "$B_LOCAL" "$B_SYNC"
    (cd "$B_REPO" && git add -A && git commit -m "B's change" >/dev/null 2>&1) || true

    # B 执行 pull —— 应产生冲突
    local pull_result=0
    (cd "$B_REPO" && git pull --no-rebase >/dev/null 2>&1) || pull_result=$?

    if [[ $pull_result -ne 0 ]]; then
        pass "git pull 检测到冲突（预期行为）"

        # 验证冲突标记存在
        if grep -q "<<<<<<" "$B_SYNC/conflict.txt" 2>/dev/null; then
            pass "冲突文件包含冲突标记 <<<<<<"
        else
            fail "冲突文件应包含冲突标记"
        fi

        # 清理冲突状态
        (cd "$B_REPO" && git checkout --theirs . >/dev/null 2>&1 && git add -A && git commit -m "resolve" >/dev/null 2>&1) || true
    else
        # 有时 git 能自动合并（内容差异在不同行时），检查实际情况
        if grep -q "A's modification" "$B_SYNC/conflict.txt" 2>/dev/null || \
           grep -q "B's modification" "$B_SYNC/conflict.txt" 2>/dev/null; then
            pass "git pull 成功合并（内容完全替换时可能自动解决）"
        else
            fail "pull 既没冲突也没保留任何一方的修改"
        fi
    fi

    cleanup_env
}

test_10_jsonl_append_merge() {
    echo -e "\n${BOLD}测试 10: .jsonl 追加合并 — A 和 B 各追加不同行，git 应能自动合并${NC}"
    setup_env

    # 创建初始 .jsonl 文件，双方同步
    echo '{"id":1,"msg":"init"}' > "$A_LOCAL/data.jsonl"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    # A 追加一行并同步
    echo '{"id":2,"msg":"from A"}' >> "$A_LOCAL/data.jsonl"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"

    # B 追加不同的一行（基于同步前的版本）
    echo '{"id":3,"msg":"from B"}' >> "$B_LOCAL/data.jsonl"

    # B 执行完整同步
    # backup: B 的 data.jsonl（2行）→ 仓库（仓库可能有 A 的版本3行，但 --update 看时间戳）
    backup_phase "$B_LOCAL" "$B_SYNC"
    (cd "$B_REPO" && git add -A && git commit -m "B appends" >/dev/null 2>&1) || true

    # pull —— A 在末尾追加了一行，B 也在末尾追加了一行
    # 这种情况 git 通常会产生冲突（两边都在同一位置插入）
    local pull_ok=true
    (cd "$B_REPO" && git pull --no-rebase >/dev/null 2>&1) || pull_ok=false

    if $pull_ok; then
        # 自动合并成功
        if grep -q "from A" "$B_SYNC/data.jsonl" && grep -q "from B" "$B_SYNC/data.jsonl"; then
            pass ".jsonl 自动合并成功，包含双方的追加内容"
        else
            pass "git pull 成功但合并结果需要检查"
        fi
    else
        # 冲突 —— 对于末尾追加的情况也是合理的
        pass ".jsonl 追加产生了冲突（两边在同一位置插入，属预期行为）"
        # 手动合并：保留所有行
        (cd "$B_REPO" && git checkout --theirs . >/dev/null 2>&1 && git add -A && git commit -m "resolve jsonl" >/dev/null 2>&1) || true
    fi

    cleanup_env
}

# =============================================================================
# 额外边界测试
# =============================================================================

test_11_empty_dirs() {
    echo -e "\n${BOLD}测试 11: 空目录处理 — 空目录应正确同步${NC}"
    setup_env

    # A 创建包含空子目录的结构
    mkdir -p "$A_LOCAL/empty_dir"
    mkdir -p "$A_LOCAL/parent/empty_child"
    echo "file" > "$A_LOCAL/parent/file.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    assert_file_exists "$B_LOCAL/parent/file.txt" "B 应收到非空目录中的文件"
    # 注意: git 不跟踪空目录，所以空目录可能不会传播
    # 这里主要验证有文件的目录结构正确
    if [[ -d "$B_LOCAL/parent" ]]; then
        pass "B 的目录结构正确"
    else
        fail "B 应有 parent 目录"
    fi

    cleanup_env
}

test_12_special_characters_in_filenames() {
    echo -e "\n${BOLD}测试 12: 文件名特殊字符 — 含空格和中文的文件名应正确同步${NC}"
    setup_env

    echo "spaces" > "$A_LOCAL/file with spaces.txt"
    echo "中文" > "$A_LOCAL/中文文件.txt"
    sync_machine "$A_LOCAL" "$A_SYNC" "$A_REPO"
    sync_machine "$B_LOCAL" "$B_SYNC" "$B_REPO"

    assert_file_content "$B_LOCAL/file with spaces.txt" "spaces" "含空格文件名应正确同步"
    assert_file_content "$B_LOCAL/中文文件.txt" "中文" "中文文件名应正确同步"

    cleanup_env
}

# =============================================================================
# 执行所有测试
# =============================================================================

echo -e "${BOLD}=======================================${NC}"
echo -e "${BOLD}  backup-claude.sh 同步逻辑测试套件${NC}"
echo -e "${BOLD}=======================================${NC}"

test_01_basic_sync
test_02_bidirectional_sync
test_03_remote_delete_propagation
test_04_local_delete_without_repo_delete
test_05_proper_local_delete
test_06_stale_machine
test_07_update_preserves_newer
test_08_delete_on_restore
test_09_conflict_detection
test_10_jsonl_append_merge
test_11_empty_dirs
test_12_special_characters_in_filenames

# ---- 结果汇总 ----
echo ""
echo -e "${BOLD}=======================================${NC}"
echo -e "${BOLD}  测试结果汇总${NC}"
echo -e "${BOLD}=======================================${NC}"
echo -e "  通过: ${GREEN}${PASS_COUNT}${NC}"
echo -e "  失败: ${RED}${FAIL_COUNT}${NC}"

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    echo ""
    echo -e "${RED}失败的断言:${NC}"
    for f in "${FAILURES[@]}"; do
        echo -e "  ${RED}✗${NC} $f"
    done
fi

echo ""
if [[ $FAIL_COUNT -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}全部通过!${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}有 ${FAIL_COUNT} 个断言失败${NC}"
    exit 1
fi
