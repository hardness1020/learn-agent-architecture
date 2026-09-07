# 21 · Loop engineering

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 次の prompt を書くのはやめましょう。あなたがいなくても agent を回す loop を設計しましょう。

これより前のセクションは、1 回のモデル呼び出しの周りに 1 つずつ機構を足してきました。このセクションはそれらを組み合わせます。

Loop engineering は、エンジニアリングの労力をどこに注ぐかの転換に付けられた名前です。
agent を turn ごとに prompt するのではなく、仕事を見つけ、agent を走らせ、出力を検査し、次に何が起きるかを決める外側のシステムを作ります。
人間は操作者から設計者に移ります。

外側の loop は次を満たす必要があります。

1. user だけでなく trigger からも実行を開始する (セクション 14)。
2. 完了と見なす前に出力を検査する。
3. 期待ではなく予算で停止する。
4. 次の実行が最初からやり直さずに続けられるよう状態を永続化する (セクション 9、12)。
5. 誰も見ていなかったときでも、何が起きたかを報告する (セクション 20)。

この層がなければ、外側の loop は人間です。人間が手で prompt し、読み、判断し、やり直します。そして人間が手を止めた瞬間に agent も止まります。

---

## 仕組み

![Mechanism diagram](assets/21-loop-engineering.png)

単純な形は、agent の loop をさらに 3 つの loop で包んだものです。それぞれが内側の 1 つを包み、それぞれが別の問いに答えます。

1. **Agent loop** (セクション 1)。タスクが終わったように見えるまで tool を呼びます。答える問い: 1 ステップはどう片付くか。
2. **検証 loop。** 出力を rubric に照らして採点します。失敗は予算の範囲でやり直しにフィードバックされます。答える問い: 本当に終わっているか。
3. **イベント loop。** cron の schedule、webhook、channel が実行を開始します (セクション 14、セクション 19)。答える問い: いつ仕事が始まるか。
4. **改善 loop。** trace と eval (セクション 20) が harness の設定、skill、モデルへの変更を供給します。答える問い: システムは良くなっているか。
   成熟した端では、この loop は harness 自体を編集します。trace から弱点を掘り出し、範囲を限った編集を提案し、回帰セットで検証します。
   loop の構造は、手で設計したテンプレートではなく探索空間になります。

データは外向きに流れます。trigger が発火して prompt をキューに入れます。agent loop が候補を作ります。採点器がそれを評価します。
失敗はフィードバックを付け足し、予算が残っている間やり直します。合格はそのタスクの channel を通じて配送されます。
その実行の trace はテレメトリに着地し、改善 loop がそれを読みます。

### 本節の新規: 検証 loop

これまでのセクションが作らなかった唯一の loop です。内側の loop は、モデルが終わったと言えば止まります。検証 loop は「終わった」を検査済みの主張に変えます。

```python
def verified_run(task, worker, checker, budget=2):    # src/verify.py
    feedback = ""
    attempts = []
    for n in range(1, budget + 1):                    # the ceiling: harness-enforced
        out = worker(task + feedback)                 # the inner loop (section 1)
        verdict = checker(task, out)                  # a separate checker (section 6)
        attempts.append({"attempt": n, "passed": verdict["passed"], "reason": verdict["reason"]})
        if verdict["passed"]:
            return {"ok": True, "output": out, "attempts": attempts}
        feedback = f"\n\nA prior attempt was rejected... Why it failed: {verdict['reason']}"
    return {"ok": False, "output": None, "attempts": attempts}   # budget spent: escalate
```

- 採点器は、まっさらな context を持つ別の agent です (セクション 6)。自分の出力を自分で採点する worker は、それを通しがちです。
  `agent_checker` がそれを 1 つ組み立てます。採点のたびに新しい `messages[]` で内側の loop を走らせ、判定の最初の単語を PASS か FAIL にします。
- rubric は loop の外で固定されています。モデルはそれを満たすことはできても、書き換えることはできません。
- フィードバックはデータです。失敗した判定は prompt の一部としてやり直しに乗るので、2 回目の試行は 1 回目が何を間違えたかを知っています。
- `ok: False` はエスカレーションの合図です。試行の記録は人間に渡ります。loop は永遠にやり直したりしません。

合格か不合格かの 1 ビットは薄い信号です。判定を 3 つの問いに割り、それぞれに根拠を名指しさせます。

- **結果。** 実行は正しい状態を残したか。根拠: 状態そのもの。コードで検査できるところはコードで検査します (セクション 23)。
- **過程。** 実行は規則に従ったか。許可された tool、必要な順序、確認の飛ばしがないこと。根拠: trace にある tool call。
- **品質。** コードの検査では表現できない部分で、答えは良いか。根拠: rubric、および不合格になった項目の名指し。

実行可能コードが採点するのは 3 つ目の問いだけです。結果と過程の検査には、何が起きたかを記録する環境が要ります。それを作るのがセクション 23 です。
分けておくと、何を直すべきかが分かります。結果が合格で過程が不合格なら、その実行はまぐれです。過程が合格で結果が不合格なら、規則の方が間違っています。

### 予算と停止条件

どの loop にも、モデルが言葉で回避できない上限が要ります。反復回数、token 予算、実時間の制限、あるいは空振りカウンタ (新しい発見がない回が K 回続いたら停止) です。

上限を強制するのは harness です。モデルにどうか止まってくださいと頼むのはヒントであって、停止条件ではありません。
`verified_run` では上限が `range()` の境界です。`budget + 1` 回目の試行は起こりえません。

### 成熟度レベル

loop engineering の出典は、どこまで任せられているかで loop を格付けします。

- **L1 · 報告。** loop は読んで報告します。行動するのは人間です。
- **L2 · 補助。** loop が変更案を書きます。承認するのは人間です。
- **L3 · 無人。** loop が行動します。人間は事後に監査します。

このレベルは permission の判断です (セクション 3)。現在のレベルでの出力が退屈なほど正しくなってから、loop を 1 段だけ上げます。

### 既存の構成への組み込み方

このセクションは新しい primitive を足しません。これまでのものの組み合わせです。

- trigger はセクション 14 の schedule とセクション 19 の channel です。
- worker はセクション 1 の loop で、作り手と検査役の分割にはセクション 6 の subagent を使います。
- 並行する loop はセクション 15 の worktree で隔離します。
- 実行と実行の間の状態は、セクション 9 の memory とセクション 12 の task 記録に置きます。
- 報告と trace はセクション 20 です。改善 loop は、セクション 20 の計測を harness の変更へと閉じます。

実行可能コードも同じつなぎ方です。`run_turn` はセクション 20 とバイト単位で同一で、検証が外からそれを包みます。

```python
def worker(prompt):                                # src/demo.py · the inner loop, unchanged
    return run_turn([{"role": "user", "content": prompt}], model, reg, Session(mode=DEFAULT))

checker = agent_checker(RUBRIC, model)             # a fresh grader agent, no tools
result = verified_run("What is 27 + 15? Use the add tool.", worker, checker, budget=2)
```

新しいのは規律の方です。完了の前に採点し、開始の前に予算を決め、常に報告します。

### さらに読む

以下はどれも `src/` にはありません。ai-agent-book と公開された自己改善の研究に由来するもので、表にあるシステムで確認が取れているわけではありません。

**学んだ変更をどこに置くか。** ある実行が、staging のデータベースには別の接続文字列が要ると突き止めたとします。それはどこへ行くのでしょうか。
改善 loop の難所は、教訓を見つけることではありません。その教訓をどこに着地させるかを選ぶことです。置き場所は 4 つあります。

- **知識ドキュメント。** 実行が 1 つ発見した事実です。書くのも消すのも安く済みます。タスクが必要とするとき agent が読み返します (セクション 9)。
- **prompt か skill。** 繰り返されるべき振る舞いです。読み込む turn すべてで context を消費します (セクション 7)。
- **プログラム。** 毎回同じように動く手順です。推論時のコストはゼロで、テストもできます (セクション 2)。
- **重み。** 最後の手段です。遅く、高く、元に戻すのが最も難しい方法です。このリポジトリの harness という主題の外側です。

規則は、その変更を収められる最小の置き場所を選ぶことです。最小であることは、検査が最も簡単で、取り消しが最も簡単でもあります。
接続文字列は事実なので、ドキュメントに入ります。system prompt には入りません。

2 番目の置き場所、prompt か skill が最も乱用されるので、そこには専用のゲートが要ります。
1 回のまずい実行ではなく、何度も起きた失敗から編集を書きます。
どんなときに適用されるかを書いて、関係のない実行では黙っているようにします。そのうえで 2 回検査します。編集に近いケースと、編集の元にしなかったホールドアウトセットです。
まずはトラフィックの一部にリリースし、ロールバックの準備を保ちます。
Karpathy はこれを system prompt learning と呼びます。重みではなく言葉を編集する、という意味です。
ACE は、prompt 全体を書き直す代わりに番号付きの context 項目を改訂することで、各編集を小さく保ちます。

**tool の利用者から tool の作り手へ。** 3 番目の置き場所であるプログラムは、これまでのセクションが作っていないものです。
skill はモデルに指示を渡しますが、モデルはそれを読んで従わなければなりません (セクション 7)。コンパイルされた workflow は、モデルなしで動くプログラムを harness に渡します。
agent が同じ種類のチケットを 10 回予約したとします。5 つのステップでそれをプログラムに変えます。

1. **記録。** うまくいった実行を 1 つ記録します。どの呼び出しを、どの順で行い、それぞれの前後で状態がどうだったかを残します。
2. **パラメータ化。** 実行ごとに変わったものを引数にします。変わらなかったものがプログラムになります。
3. **リセット状態で検証。** まっさらな環境で再生します (セクション 23)。各ステップに、実行前の検査、実行後の検査、そして最終状態の検査を付けます。
4. **再生。** 次に条件が合うタスクが来たら、プログラムをそのまま最後まで走らせます。モデル呼び出しがないので、速く、安く、毎回同じです。
5. **無効化。** 検査が 1 つでも失敗したらプログラムを引退させます。タスクはモデルに戻り、モデルは新しいものを記録できます。

tool を作るのは、同じライフサイクルを反対の端から見たものです。agent は自分にできないことにぶつかり、ライブラリを見つけ、
それを tool として包み、registry が受け入れる前に検証します (セクション 2)。
どちらの動きも、高くつく探索を 1 回だけ行い、検査可能で安い能力に変えます。
どちらにもステップ 5 が要ります。作った相手のサイトや API は変わるからです。

**harness を編集する。** loop が prompt だけでなく harness のコードを変えたいとします。その場合、パッチを与える前に契約が要ります。
変更契約が述べるのは 4 つです。どの trace がどのくらいの頻度で失敗したか、根本原因、その変更が何を良くするはずか、そしてどう元に戻すかです。
契約がなければパッチもありません。契約を読むのは人間で、それが自己編集 loop と、誰にも監査できない loop とを分けます。
loop が編集してよいコードは前もって宣言しておきます。permission、予算、ゲートはその領域の外に置き、loop が手を届かせられないようにします (セクション 3)。

loop がどこを探索するかは梯子になっています。一番下の段は prompt の 1 つの規則です。
その上は context の組み立て方、次に workflow、次に harness のコード、次に変更を提案するコードです。
下の段が行き詰まったときだけ 1 段上ります。1 段上がるごとに探索は広がり、それにかけられる検査は弱くなります。
prompt の 1 規則なら 1 日で A/B テストできます。変更を提案するコードを差し替えれば、その後のあらゆる変更の提案され方が変わります。

**オンラインの実行、オフラインの学習器。** この 2 つは分けておきます。オンラインの loop はタスクを実行し、何が起きたかを記録します。
教訓を引き出したり、skill を昇格させたり、prompt を編集したりはしません。
別のオフライン loop が多数の実行をまとめて読み、繰り返される失敗を見つけ、変更の候補を書き、検証し、バージョンをリリースします。

この分離が、1 回の実行が agent を書き換えるのを止めます。まぐれで通った経路 1 本はパターンではありません。
agent に何を覚えるべきか告げた web ページは、根拠ではありません。
複数の実行にまたがって同じ信号が出ることと、検証ゲートを通ることを要求すれば、どちらもリリースには入りません。

この分離は、測るものも変えます。1 つではなく 2 つの数字を読みます。

- **更新。** loop は良い候補を作れているか。いくつ提案し、いくつが検証を通り、いくつがロールバックされたか。
- **効果。** リリースされた変更は役に立っているか。その変更は狙った実行で読み込まれているか、agent はそれに従っているか、ホールドアウトの性能は動いたか。

両方読みます。1 つ目だけだと、正しいのに一度も読み込まれない skill が更新の失敗に見えてしまい、loop は自分について間違った結論を引き出します。

---

## システム別

各 agent が外側の loop をどう組み合わせるか。

| | Claude Code | Hermes Agent | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- | --- |
| **利点** | スクリプト化した検証と硬い予算。 | 予算に加えて、ロールバック付きの改善 loop。 | 実行ごとに硬い請求上限。 | 外側の loop が、公開イベント上の plugin として接続する。 |
| **欠点** | ソースには閉じた改善 loop がない。 | 採点してやり直す loop は組み込まれていない。 | 予算の側だけ。 | 仕事を検査するものがない。予算はラウンド数だけ。 |
| **理由** | 外側の loop は、自分でスクリプトを書くプログラム。 | 改善はモデルまで届くべきもの。 | 1 回の実行が 1 つの採点対象タスク。 | loop 自体が plugin なので、制御はそこに接続する。 |
| **方法: verification** | スクリプト化した段階と judge のパネル。 | 作り手と検査役、加えてオフラインのテスト。 | なし。SWE-bench がオフラインで採点する。 | 組み込みはなし。完了は自己申告。 |
| **方法: event loop** | cron、起床、リモートの trigger。 | tool を制限した cron。 | なし。ランナーが時刻ではなくタスクを割り当てる。 | リマインダがログから turn として再生される。 |
| **方法: improvement loop** | 再開可能な workflow がキャッシュから再生される。 | 実行が学習データになる。 | なし。予算だけ。 | リリース済みのものはなし。接続点は存在する。 |

---

## 失敗モード

- **停止条件がない。** 上限のないやり直し loop は、誰かが請求に気付くまで token を燃やします。緩和策: harness が強制する反復、token、時間の予算。
- **自己採点。** worker が自分の出力を通してしまい、検証 loop は何も検証しません。緩和策: 別の検査役 agent と、loop の外で固定した rubric。
- **形だけの rubric。** 常に合格させる採点器は、ないよりも悪いです。まずい出力に検証済みというラベルを貼るからです。
  緩和策: 敵対的な検証 (検査役に反証するよう prompt する) と、定期的な人間の抜き取り検査。
- **無人化が早すぎる。** L1 の報告が一度も検査されないうちに、loop が L3 の書き込み権限を得ます。
  緩和策: 成熟度の梯子を 1 段ずつ上り、セクション 3 の permission でゲートします。
- **静かなずれ。** 無人の loop が劣化しても、誰もその出力を読みません。緩和策: heartbeat、必ず配送される報告、合格率とコストに関するセクション 20 のメトリクス。
- **状態の忘却。** どの実行も同じ仕事を発見し直し、やり直します。緩和策: 発見を memory か task 記録に永続化し (セクション 9、12)、実行開始時に読みます。
- **自己編集する harness がゲートを抜け出す。** harness のコードを変更できる改善 loop は、自分をゲートしているコードも変更できます。
  緩和策: permission と予算を、loop が編集できるものの外に置きます (セクション 3)。
- **代理目標のずれ。** 終わりの決まっていない仕事では、rubric は本当の目標の代役にすぎません。loop は代わりに rubric を満たすことを学びます。
  見慣れたコードを使い回し、ノイズを発見と読み、通った実行だけを残します。スコアは上がり、本当の目標は遠のきます。
  緩和策: 失敗した実行を根拠に残し、ホールドアウトセットを更新し、人間が出力を本当の目標に照らして検査します。
- **どの教訓も prompt の編集になる。** prompt は最も書きやすい場所なので、何もかもそこに集まります。やがて自分の規則同士が食い違うほど膨れます。
  緩和策: 教訓が何であるかで置き場所を選びます。事実はドキュメント、手順はプログラム、prompt は繰り返されるべき振る舞いのためのものです。
- **コンパイル済み workflow が環境より長生きする。** サイトや API が変わったのに、プログラムはそのまま再生します。モデルよりも速く誤った状態を書き込みます。
  緩和策: 再生の各ステップの前後で検査し、最初に検査が失敗した時点でプログラムを引退させます。
- **オンラインの実行が自分の教訓を昇格させる。** 実行中に教訓を引き出す agent は、たまたまうまくいった経路を昇格させたり、覚えさせる目的で仕込まれた信頼できないページの文言を昇格させたりします。
  緩和策: オンラインの loop には根拠の記録だけをさせます。候補は、別のオフラインのパスがリリース前に検証します。

---

## 実行

[`src/`](src/) は 20 を引き継ぎ、次を追加します。

- [`verify.py`](src/verify.py): 検証 loop (`verified_run`: 採点、フィードバック付きのやり直し、予算、エスカレーション) と、判定ごとに新しい採点器を作る `agent_checker`。
- [`test.py`](src/test.py): 一発合格、フィードバックがやり直しに届くこと、予算の上限、PASS/FAIL の判定契約についてのオフライン検査。
- [`demo.py`](src/demo.py): 検証付きの実行を 1 回。add tool を持つ worker、固定の rubric で採点する別の検査役、予算を使い切ったときのエスカレーション。

loop は変わりません。検証が外からそれを包みます。

```bash
python sections/21-loop-engineering/src/test.py         # offline checks, no key
uv run python sections/21-loop-engineering/src/demo.py  # live demo, needs a key
```

---

## 出典

- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `docs/subsystems/core.md`、`packages/workflow/tool-ralph/README.md`、`packages/schedule/schedule/README.md`、`docs/subsystems/goal.md`。
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): 構成要素と成熟度レベル。
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering): 積み重なった 4 つの loop。
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): 組み合わせられる構成要素。
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): ゴール条件。
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/): 改善 loop の詳細と、loop の外に置くゲート。
- [ai-agent-book · chapter 8](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter8.md) (《深入理解 AI Agent》、李博杰。中国語原文が正典):
  3 層の検証、学んだ変更をどこに着地させるかとその規則、prompt learning のゲート、変更契約、
  メタ最適化の梯子、オンラインとオフラインの分離、進化メトリクスの分離、検証可能な loop の境界。
- [PreAct](https://arxiv.org/abs/2606.17929): trajectory を、事前・事後・保存前の検査付きでパラメータ化された workflow にコンパイルし、モデルなしで再生する話。
  第一著者名が本の著者と同じなので、報告されている再生の高速化 (およそ 8.5 倍から 13 倍) は単一ソースとして読んでください。
- [Alita](https://arxiv.org/abs/2505.20286): 能力の欠落が tool の作成を引き起こし、tool がライブラリに入る前に検証される話。
- Karpathy · "system prompt learning" (X、2025 年 5 月 11 日): 重みではなく言葉を編集することを、第 3 の学習パラダイムとして名付けた話。
- [ACE](https://arxiv.org/abs/2510.04618): prompt 全体の書き直しではなく、安定した id を持つ context 項目を逐次更新する話。
- [Lin et al.](https://arxiv.org/abs/2605.30621): harness の更新と harness の効果を別々に測り、モデルの入れ替えで両者を切り分ける話。
- [AHE](https://arxiv.org/abs/2604.25850) と [Self-Harness](https://arxiv.org/abs/2606.09498): 自己編集する harness のための変更契約と、範囲を限った候補空間。
- [Claude Code](https://code.claude.com/docs): `/loop`、`ScheduleWakeup`、`Workflow` スキーマ。ソースのバックアップではなく、tool スキーマと文書化された挙動から。
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `agent/iteration_budget.py`、`cron/scheduler.py`、`tools/skill_manager_tool.py`、`hermes_cli/curator.py`、`agent/trajectory.py`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py` の `AgentConfig` と `query()`、`agents/interactive.py`、`run/benchmarks/swebench.py`。
