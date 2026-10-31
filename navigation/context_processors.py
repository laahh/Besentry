from .menu import build_menu


def sidebar_menu(request):
    # Callable, so the menu (and its group query) is only built when a template uses it.
    return {'sidebar_menu': lambda: build_menu(request.user)}
