from .architect_prompts import ArchitectPrompts
from .ask_coder import AskCoder
from .base_coder import Coder


class ArchitectCoder(AskCoder):
    edit_format = "architect"
    gpt_prompts = ArchitectPrompts()
    auto_accept_architect = False

    def get_edits(self, mode="update"):
        """自动模式下，把计划回复里的 shell 代码块也交给自动执行流程。"""
        if getattr(self.io, "_auto_mode", False) and self.partial_response_content:
            try:
                from .editblock_coder import find_original_update_blocks

                blocks = find_original_update_blocks(
                    self.partial_response_content,
                    self.fence,
                    self.get_inchat_relative_files(),
                )
                self.shell_commands += [block[1] for block in blocks if block[0] is None]
            except ValueError:
                # 计划里可能有示例代码块，解析失败就跳过，不影响正常流程。
                pass
        return []

    def reply_completed(self):
        content = self.partial_response_content

        if not content or not content.strip():
            return

        if not self.auto_accept_architect and not self.io.confirm_ask("Edit the files?"):
            return

        kwargs = dict()

        # Use the editor_model from the main_model if it exists, otherwise use the main_model itself
        editor_model = self.main_model.editor_model or self.main_model

        kwargs["main_model"] = editor_model
        kwargs["edit_format"] = self.main_model.editor_edit_format
        # 自动模式下让编辑器一并建议并执行 shell 命令（Claude Code 风格的 auto）。
        kwargs["suggest_shell_commands"] = bool(getattr(self.io, "_auto_mode", False))
        kwargs["map_tokens"] = 0
        kwargs["total_cost"] = self.total_cost
        kwargs["cache_prompts"] = False
        kwargs["num_cache_warming_pings"] = 0
        kwargs["summarize_from_coder"] = False

        new_kwargs = dict(io=self.io, from_coder=self)
        new_kwargs.update(kwargs)

        editor_coder = Coder.create(**new_kwargs)
        editor_coder.cur_messages = []
        editor_coder.done_messages = []

        if self.verbose:
            editor_coder.show_announcements()

        editor_coder.run(with_message=content, preproc=False)

        self.move_back_cur_messages("I made those changes to the files.")
        self.total_cost = editor_coder.total_cost
        self.aider_commit_hashes = editor_coder.aider_commit_hashes
