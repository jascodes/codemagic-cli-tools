#!/usr/bin/env python3
from __future__ import annotations

import argparse
import enum
import inspect
import types
from typing import NoReturn
from typing import Optional
from typing import Sequence
from typing import Type

from codemagic.cli.colors import Colors

from .argument_formatter import ArgumentFormatter
from .argument_properties import ArgumentProperties


class Argument(ArgumentProperties, enum.Enum):

    @classmethod
    def with_custom_argument_group(
            cls,
            argument_group_name: str,
            *arguments: Argument,
            exclude: Sequence[Argument] = tuple(),
    ):
        """
        Make duplicates of given arguments with a specified argument group name.
        Duplicate arguments will have all the same properties as the original except the
        argument_group_name attribute. Note that also the enumeration class name and
        value attribute name are persisted, but comparison to the original enumeration
        will fail.
        """
        # https://docs.python.org/3/library/enum.html#functional-api
        # Use the functional API that enum module provides to make duplicates of
        # the given Argument enumerations with updated values.
        for argument in arguments:
            if argument in exclude:
                continue
            updated_properties = cls.duplicate(
                argument,
                argument_group_name=argument_group_name,
            )
            argument_class: Type[Argument] = Argument(  # type: ignore
                argument.__class__.__name__,
                {argument.name: updated_properties},  # type: ignore
            )
            new_argument = argument_class[argument.name]
            new_argument.register = argument.register  # type: ignore
            new_argument._get_parser_argument = argument._get_parser_argument  # type: ignore
            new_argument._set_parser_argument = argument._set_parser_argument  # type: ignore
            yield new_argument

    @classmethod
    def resolve_optional_two_way_switch(
        cls,
        is_switched_on: Optional[bool],
        is_switched_off: Optional[bool],
    ) -> Optional[bool]:
        if {is_switched_on, is_switched_off} in ({True}, {False}):
            raise ValueError('Neither of the switches, or exactly one can be truthy at the time')
        if is_switched_on is True:
            return True
        if is_switched_off is True:
            return False
        return None

    @property
    def flag(self) -> str:
        return sorted(self.value.flags, key=len, reverse=True)[0]

    def register(self, argument_group: argparse._ArgumentGroup):
        kwargs = self.value.argparse_kwargs or {}
        if 'action' not in kwargs:
            kwargs['type'] = self.value.type
        if self.argument_group_name is None:
            parser_argument = argument_group.add_argument(
                *self.value.flags,
                help=self.get_description().replace('`', ''),
                dest=self.value.key,
                **kwargs,
            )
            self._set_parser_argument(parser_argument)

    def is_required(self) -> bool:
        return (self.value.argparse_kwargs or {}).get('required', True)

    def get_default(self):
        return (self.value.argparse_kwargs or {}).get('default', None)

    def from_args(self, cli_args: argparse.Namespace, default=None):
        value = vars(cli_args)[self.value.key] or default
        if not value and self._is_typed_cli_argument():
            try:
                return self.value.type.from_environment_variable_default()
            except ValueError as ve:
                self.raise_argument_error(str(ve))
        return value

    def get_description(self) -> str:
        description = self.value.description
        try:
            default_value = (self.value.argparse_kwargs or {})['default']
            if default_value is not None:
                description = f'{description} {ArgumentFormatter.format_default_value(default_value)}'
        except KeyError:
            pass

        if self._is_function_argument():
            return description
        elif self._is_typed_cli_argument():
            return self.value.type.get_description(self.value)
        else:
            return description

    def get_missing_value_error_message(self) -> str:
        if self._is_typed_cli_argument():
            message = self.value.type.get_missing_value_error_message(self)
            if message:
                return message
        message = f'Value {Colors.CYAN(self.key.upper())} not provided'
        if self.flags:
            flags = ','.join(self.flags)
            message = f'{message} for {Colors.BRIGHT_BLUE(flags)}'
        return message

    def raise_argument_error(self, message: Optional[str] = None) -> NoReturn:
        """
        :param message: ArgumentError message
        :raises: argparse.ArgumentError
        """
        if message is None:
            message = self.get_missing_value_error_message()
        raise argparse.ArgumentError(self._get_parser_argument(), message)

    def _is_function_argument(self):
        return isinstance(self.value.type, (types.FunctionType, types.MethodType))

    def _is_typed_cli_argument(self):
        from .typed_cli_argument import TypedCliArgument
        return inspect.isclass(self.value.type) and issubclass(self.value.type, TypedCliArgument)
