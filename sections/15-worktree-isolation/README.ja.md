# 15 · Worktree isolation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 並列に動く agent へ、それぞれ別の作業ディレクトリを与えます。

作業ディレクトリが 1 つしかない場合、それは共有された可変の状態です。2 つの agent が同じファイルへ同時に書き込むと、一方がもう一方の成果を上書きしてしまいます。

どんな作業が存在するかは task system が決めます。作業をどう分割するかは subagent が決めます。
worktree isolation は書き込みを分離します。各 agent が自分のディレクトリの中で書き込むので、互いに干渉しません。

作業の単位ごとに、専用のチェックアウトとブランチを割り当てます。agent のファイル系ツールと shell 系ツールは、そのチェックアウトの中でパスを解決します。

isolation 層に必要な機能は次のとおりです。

1. 作業の単位ごとに専用のチェックアウトを作る。
2. ツールをそのチェックアウトに結び付ける。
3. worktree のルートから外へ出てしまう名前を拒否する。
4. clean な worktree は削除し、dirty なものはレビュー用に残す。

この層がないと、同じディレクトリを同時に編集する agent どうしが、互いのファイルを壊してしまいます。

---

## 仕組み

![Mechanism diagram](assets/15-worktree-isolation.png)

構成要素は 2 つです。

1. 作業の単位ごとに用意する専用の git worktree。
2. context 単位の作業ディレクトリの束縛。

この束縛は agent の context に限定しなければなりません。プロセス全体の `chdir` を使うと、同じプロセス内の他の agent にも影響します。

- 各 worktree は同じリポジトリを、それぞれ別のブランチでチェックアウトしたものです。
- slug はそのままパスになるので、パスを連結する前に検証します。
- ツールはプロセス全体の cwd ではなく、context から `get_cwd()` を読みます。
- 後片付けでは clean な worktree だけを削除します。dirty な worktree はレビュー用に残ります。

### 新規: worktree と cwd の束縛

`worktree.py` は slug を検証し、worktree を作り、context 変数を通じて cwd を束縛します。

```python
_cwd = contextvars.ContextVar("cwd", default=None)   # per-context cwd

@contextlib.contextmanager
def cwd_override(path):
    token = _cwd.set(str(path))                       # bind, never os.chdir
    try:
        yield
    finally:
        _cwd.reset(token)

def remove(repo_root, slug, force=False):
    path = _path(repo_root, slug)                     # _path validates the slug first
    if not force and changes(path):
        return False                                  # keep for review
    _git(repo_root, "worktree", "remove", "--force", str(path))
    _git(repo_root, "branch", "-D", f"worktree-{slug}")
    return True
```

- `cwd_override` は現在の context にだけ効きます。
- ツールは `get_cwd()` をサブプロセスやファイル操作へ渡します。
- `create` は `git worktree add -B worktree-<slug>` を実行します。
- `validate_slug` はディレクトリトラバーサルと許可されない文字を拒否します。
- `remove` は強制指定がない限り、dirty な worktree の削除を拒否します。

### 組み込み方

isolation は loop の外側から turn を包みます。

```python
wt = worktree.create(repo, "agent-1")                 # src/demo.py
with worktree.cwd_override(wt):
    run_turn([{"role": "user", "content": prompt}], model, reg, session)
worktree.remove(repo, "agent-1")                       # clean -> remove, dirty -> keep
```

loop と subagent の経路に特別な処理は要りません。変わるのは、ツールから見える作業ディレクトリだけです。

これをモデル側から選べるようにするには、`Agent` tool のスキーマに `isolation` オプションを追加し、`spawn` で分岐させます。

---

## システム別

各システムが並列作業をどう分離し、どう後片付けするかを示します。

| | Claude Code |
| --- | --- |
| **利点** | ファイルシステムのレベルで本当に分離でき、差分もきれいです。dirty な worktree はレビュー用に残るので、成果が黙って失われません。 |
| **欠点** | worktree はディスク、セットアップ時間、そして後段のマージ作業というコストを伴います。 |
| **理由** | 複数の agent が 1 つの共有ディレクトリへ安全に書き込むことはできないので、作業の単位ごとに専用のチェックアウトの中で書き込みます。 |
| **方法: 分離の単位** | task または session ごとに 1 つの git worktree を作り、それぞれ別のブランチに置きます。モデルは subagent を spawn するときに worktree を要求できます。 |
| **方法: 束縛** | subagent には限定された cwd を与えるので、同時に動く agent どうしが影響し合いません。session モードではプロセスの cwd 自体を変えます。task のレコードに束縛を保存することはありません。 |
| **方法: 後片付け** | clean な worktree は削除します。dirty なものは、ユーザーが明示的に変更を破棄しない限り残します。定期的な掃除で、古い一時 worktree を削除します。 |

---

## 失敗モード

- **slug 経由のパストラバーサル。** パスの連結や git コマンドの前に検証。
- **削除による無言の消失。** ユーザーが明示的に変更を破棄しない限り、dirty な worktree は保持。
- **agent 間での cwd の漏れ。** 同時に動く subagent には context ローカルな cwd を使用。
- **古い worktree の滞留。** 一時 worktree として把握しているものだけを掃除。
- **fork 後の古い読み取り。** fork した child agent には、worktree の中のファイルを読み直すよう指示。

---

## 実行

[`src/`](src/) は 14 を引き継いだうえで、次を追加します。

- [`worktree.py`](src/worktree.py): slug の検証、worktree の作成、context ローカルな cwd、安全な削除。
- [`test.py`](src/test.py): 2 つの agent が別々の worktree で書き込むこと、clean と dirty で削除が分かれることを確認します。
- [`demo.py`](src/demo.py): worktree の中で実際の turn を 1 回実行します。

loop と subagent の経路は変わりません。isolation は cwd を束縛することで turn を包みます。

```bash
python sections/15-worktree-isolation/src/test.py         # offline checks, real git, no key
uv run python sections/15-worktree-isolation/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/EnterWorktreeTool/`, `tools/ExitWorktreeTool/`, `utils/worktree.ts`, `utils/cwd.ts`, `tools/AgentTool/AgentTool.tsx`.
- [learn-claude-code · s18_worktree_isolation](https://github.com/shareAI-lab/learn-claude-code): section framing.
