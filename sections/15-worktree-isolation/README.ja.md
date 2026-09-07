# 15 · Worktree isolation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 並列エージェントに個別の作業ディレクトリを与えます。

単一の作業ディレクトリは共有可変状態です。 2 人のエージェントが同時に同じファイルに書き込む場合、一方が他方の作業を上書きする可能性があります。

タスク システムは、どのような作業が存在するかを決定します。 Subagents 作業をどのように分割するかを決定します。
Worktree isolation は書き込みを分離します。各エージェントは独自のディレクトリに書き込むため、干渉しません。

各作業単位には独自のチェックアウトとブランチが与えられます。エージェントのファイルおよびシェル ツールは、そのチェックアウト内のパスを解決します。

絶縁層は次のことを行う必要があります。

1. 作業単位ごとにプライベート チェックアウトを作成します。
2. ツールをそのチェックアウトにバインドします。
3. worktree ルートをエスケープする名前を拒否します。
4. きれいな worktrees を取り外し、汚れたものはレビュー用に保管しておきます。

このレイヤーがないと、同じディレクトリを同時に編集するエージェントが互いのファイルを破損する可能性があります。

---

## メカニズム

![機構図](assets/15-worktree-isolation.png)

2 つの部分があります:

1. 作業単位ごとのプライベート git worktree。
2. コンテキストごとの作業ディレクトリのバインディング。

バインディングのスコープはエージェント コンテキストに設定する必要があります。グローバル `chdir` は、同じプロセス内の他のエージェントに影響を与えます。

- 各 worktree は、独自のブランチ上の同じリポジトリのチェックアウトです。
- スラグはパスになるため、パスが結合する前に検証してください。
- ツールは、グローバル プロセス cwd からではなく、コンテキストから `get_cwd()` を読み取ります。
- 分解では、クリーンな worktrees のみが削除されます。汚い worktrees レビュー用に滞在します。

### 新機能: worktree と cwd バインディング

`worktree.py` はスラッグを検証し、worktree を作成し、コンテキスト変数を介して cwd をバインドします。

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

- `cwd_override` は、現在のコンテキストにのみ影響します。
- ツールは `get_cwd()` をサブプロセスとファイル操作に渡します。
- `create` は `git worktree add -B worktree-<slug>` を実行します。
- `validate_slug` は、トラバーサル文字と許可されていない文字を拒否します。
- `remove` は、強制されない限り、ダーティ worktree の削除を拒否します。

### 統合方法

アイソレーションはループの外側からターンをラップします。

```python
wt = worktree.create(repo, "agent-1")                 # src/demo.py
with worktree.cwd_override(wt):
    run_turn([{"role": "user", "content": prompt}], model, reg, session)
worktree.remove(repo, "agent-1")                       # clean -> remove, dirty -> keep
```

ループと subagent パスには特別なロジックは必要ありません。ツールによって認識される作業ディレクトリのみが変更されます。

このモデルを選択可能にするには、`isolation` オプションを `Agent` ツール スキーマに追加し、`spawn` に分岐します。

---

## システムごと

各システムが並列作業をどのように分離し、クリーンアップするか。

| | Claude Code |
| --- | --- |
| **長所** |実際のファイルシステムの分離とクリーンな差分。ダーティ worktrees はレビューのために残るため、作業が黙って失われることはありません。 |
| **短所** | Worktrees コストのディスク、セットアップ時間、およびその後のマージ ステップ。 |
| **理由** |複数のエージェントが 1 つの共有ディレクトリに安全に書き込むことができないため、各作業単位が独自のチェックアウトに書き込みます。 |
| **方法: 隔離ユニット** | Git worktree タスクまたはセッションごとに、それぞれ独自のブランチ上にあります。モデルは、subagent を生成するときにこれを要求できます。 |
| **方法: バインディング** | subagents のスコープ付き cwd なので、同時エージェントは相互に影響しません。セッションモードはプロセス cwd を変更します。タスク レコードはバインディングを保存しません。 |
| **方法: クリーンアップ** |クリーンな worktrees を削除します。ユーザーが明示的に変更を破棄しない限り、ダーティなものはそのままにしておきます。定期的なスイープにより、古い一時的な worktrees が削除されます。 |

---

## 障害モード

- **スラッグ内のパス トラバーサル。** パス結合または git コマンドの前に検証します。
- **削除時のサイレント損失** ユーザーが明示的に変更を破棄しない限り、worktrees をダーティのままにしておきます。
- **エージェント間での cwd リーク。** 同時 subagents にはコンテキスト ローカル cwd を使用します。
- **古い worktree ビルドアップ。** 既知の一時的な worktrees のみをスイープします。
- **フォーク後の古い読み取り。** フォークされた子に、worktree 内のファイルを再読み取りするように指示します。

---

## 実行可能

[`src/`](src/) は 14 を繰り上げて次を追加します。

- [`worktree.py`](src/worktree.py): スラグ検証、worktree 作成、コンテキスト ローカル CWD、および安全な削除。
- [`test.py`](src/test.py): 別々の worktrees とクリーン/ダーティ除去ゲートに書き込む 2 つのエージェントをチェックします。
- [`demo.py`](src/demo.py): worktree 内でライブ ターンを実行します。

ループと subagent パスは変更されません。分離は cwd をバインドすることでターンをラップします。

```bash
python sections/15-worktree-isolation/src/test.py         # offline checks, real git, no key
uv run python sections/15-worktree-isolation/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `tools/EnterWorktreeTool/`、`tools/ExitWorktreeTool/`、`utils/worktree.ts`、`utils/cwd.ts`、`tools/AgentTool/AgentTool.tsx`。
- [learn-claude-code · s18_worktree_isolation](https://github.com/shareAI-lab/learn-claude-code): セクション フレーム。
