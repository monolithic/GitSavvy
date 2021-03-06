from collections import OrderedDict

import sublime
from sublime_plugin import TextCommand, EventListener

from . import util


interfaces = {}
subclasses = []


class Interface():

    interface_type = ""
    read_only = True
    syntax_file = ""
    word_wrap = False

    dedent = 0
    skip_first_line = False

    regions = []
    template = ""

    _initialized = False

    def __new__(cls, repo_path=None, **kwargs):
        """
        Search for intended interface in active window - if found, bring it
        to focus and return it instead of creating a new interface.
        """
        window = sublime.active_window()
        for view in window.views():
            vset = view.settings()
            if vset.get("git_savvy.interface") == cls.interface_type and \
               vset.get("git_savvy.repo_path") == repo_path:
                window.focus_view(view)
                return interfaces[view.id()]

        return super().__new__(cls)

    def __init__(self, repo_path=None, view=None):
        if self._initialized:
            return
        self._initialized = True

        subclass_attrs = (getattr(self, attr) for attr in vars(self.__class__).keys())

        self.partials = {
            attr.key: attr
            for attr in subclass_attrs
            if callable(attr) and hasattr(attr, "key")
            }

        if self.skip_first_line:
            self.template = self.template[self.template.find("\n") + 1:]
        if self.dedent:
            for attr in vars(self.__class__).keys():
                if attr.startswith("template"):
                    setattr(self, attr, "\n".join(
                        line[self.dedent:] if len(line) >= self.dedent else line
                        for line in getattr(self, attr).split("\n")
                        ))

        if view:
            self.view = view
            self.render(nuke_cursors=False)
        else:
            self.view = self.create_view(repo_path)

        interfaces[self.view.id()] = self

    def create_view(self, repo_path):
        window = sublime.active_window()
        self.view = window.new_file()

        self.view.settings().set("git_savvy.repo_path", repo_path)
        self.view.set_name(self.title())
        self.view.settings().set("git_savvy.{}_view".format(self.interface_type), True)
        self.view.settings().set("git_savvy.interface", self.interface_type)
        self.view.settings().set("word_wrap", self.word_wrap)
        self.view.set_syntax_file(self.syntax_file)
        self.view.set_scratch(True)
        self.view.set_read_only(self.read_only)
        util.view.disable_other_plugins(self.view)

        self.render()
        window.focus_view(self.view)

        return self.view

    def render(self, nuke_cursors=True):
        if self.regions:
            self.clear_regions()
        if hasattr(self, "pre_render"):
            self.pre_render()

        rendered = self.template

        self.regions = []
        keyed_content = self.get_keyed_content()
        for key, new_content in keyed_content.items():
            interpol = "{" + key + "}"
            interpol_len = len(interpol)
            cursor = 0
            match = rendered.find(interpol)
            while match >= 0:
                self.adjust(self.regions, match, interpol_len, len(new_content))
                self.regions.append((key, [match, match+len(new_content)]))
                rendered = rendered[:match] + new_content + rendered[match+interpol_len:]

                match = rendered.find(interpol, cursor)

        self.view.run_command("gs_new_content_and_regions", {
            "content": rendered,
            "regions": self.regions,
            "nuke_cursors": nuke_cursors
            })

    @staticmethod
    def adjust(regions, idx, orig_len, new_len):
        """
        When interpolating template variables, update region ranges for previously-evaluated
        variables, but which occur later on in the output/template string.
        """
        diff = new_len - orig_len
        for region in regions:
            if region[1][0] > idx:
                region[1][0] += diff
                region[1][1] += diff

    def get_keyed_content(self):
        keyed_content = OrderedDict(
            (key, render_fn())
            for key, render_fn in self.partials.items()
            )

        for key in keyed_content:
            output = keyed_content[key]
            if isinstance(output, tuple):
                sub_template, complex_partials = output
                keyed_content[key] = sub_template

                for render_fn in complex_partials:
                    keyed_content[render_fn.key] = render_fn()

        return keyed_content

    def update(self, key, content):
        self.view.run_command("gs_update_region", {
            "key": "git_savvy_interface." + key,
            "content": content
            })

    def clear_regions(self):
        for key, region_range in self.regions:
            self.view.erase_regions(key)

    def get_selection_line(self):
        selections = self.view.sel()
        if not selections or len(selections) > 1:
            sublime.status_message("Please make a selection.")
            return None

        selection = selections[0]
        return selection, util.view.get_lines_from_regions(self.view, [selection])[0]


def partial(key):
    def decorator(fn):
        fn.key = key
        return fn
    return decorator


class GsNewContentAndRegionsCommand(TextCommand):

    def run(self, edit, content, regions, nuke_cursors=False):
        cursors_num = len(self.view.sel())
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(is_read_only)

        if not cursors_num or nuke_cursors:
            selections = self.view.sel()
            selections.clear()
            pt = sublime.Region(0, 0)
            selections.add(pt)

        for key, region_range in regions:
            a, b = region_range
            self.view.add_regions("git_savvy_interface." + key, [sublime.Region(a, b)])


class GsUpdateRegionCommand(TextCommand):

    def run(self, edit, key, content):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        for region in self.view.get_regions(key):
            self.view.replace(edit, region, content)
        self.view.set_read_only(is_read_only)


def register_listeners(InterfaceClass):
    subclasses.append(InterfaceClass)


def get_interface(view_id):
    return interfaces.get(view_id, None)


class GsInterfaceCloseCommand(TextCommand):

    """
    Clean up references to interfaces for closed views.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        view_id = self.view.id()
        if view_id in interfaces:
            del interfaces[view_id]


class GsInterfaceRefreshCommand(TextCommand):

    """
    Re-render GitSavvy interface view.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface_type = self.view.settings().get("git_savvy.interface")
        for InterfaceSubclass in subclasses:
            if InterfaceSubclass.interface_type == interface_type:
                existing_interface = interfaces.get(self.view.id(), None)
                if existing_interface:
                    existing_interface.render(nuke_cursors=False)
                else:
                    interface = InterfaceSubclass(view=self.view)
                    interfaces[interface.view.id()] = interface
