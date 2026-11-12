! Derived from WSJT-X lib/crc.f90 (https://github.com/WSJTX/wsjtx).
! Copyright the WSJT Development Team and the original authors.
! Licensed under the GNU General Public License, version 3 (GPL-3.0).
! https://www.gnu.org/licenses/gpl-3.0.txt
!
! Checked 2026-11-10 against WSJT-X master: the code matches lib/crc.f90
! aside from newline convention. That upstream file has no per-file
! copyright line, so no individual author is named here. See NOTICE.

module crc 
  use, intrinsic :: iso_c_binding, only: c_int, c_loc, c_int8_t, c_bool, c_short
  interface

    function crc14 (data, length) bind (C, name="crc14")
      use, intrinsic :: iso_c_binding, only: c_short, c_ptr, c_int
      implicit none
      integer (c_short) :: crc14
      type (c_ptr), value :: data
      integer (c_int), value :: length
    end function crc14
 
    function crc14_check (data, length) bind (C, name="crc14_check")
      use, intrinsic :: iso_c_binding, only: c_bool, c_ptr, c_int
      implicit none
      logical (c_bool) :: crc14_check
      type (c_ptr), value :: data
      integer (c_int), value :: length
    end function crc14_check

    function crc13 (data, length) bind (C, name="crc13")
      use, intrinsic :: iso_c_binding, only: c_short, c_ptr, c_int
      implicit none
      integer (c_short) :: crc13
      type (c_ptr), value :: data
      integer (c_int), value :: length
    end function crc13
 
    function crc13_check (data, length) bind (C, name="crc13_check")
      use, intrinsic :: iso_c_binding, only: c_bool, c_ptr, c_int
      implicit none
      logical (c_bool) :: crc13_check
      type (c_ptr), value :: data
      integer (c_int), value :: length
    end function crc13_check

    function crc10 (data, length) bind (C, name="crc10")
      use, intrinsic :: iso_c_binding, only: c_short, c_ptr, c_int
      implicit none
      integer (c_short) :: crc10
      type (c_ptr), value :: data
      integer (c_int), value :: length
    end function crc10

    function crc10_check (data, length) bind (C, name="crc10_check")
      use, intrinsic :: iso_c_binding, only: c_bool, c_ptr, c_int
      implicit none
      logical (c_bool) :: crc10_check
      type (c_ptr), value :: data
      integer (c_int), value :: length
    end function crc10_check

  end interface
end module crc
